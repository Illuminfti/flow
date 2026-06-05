"""The script-authoring API — what a workflow script calls.

A script is a Python module exposing ``run(wf, args)`` (and optional ``META``).
``wf`` is a :class:`Workflow` bound to an :class:`~flowleaf.scheduler.Engine`.

    def run(wf, args):
        wf.phase("scan")
        findings = wf.parallel([
            lambda d=d: wf.agent(f"review {d}", label=f"r:{d}", schema=FINDINGS)
            for d in args["dimensions"]
        ])
        return {"findings": [f for f in findings if f]}

``parallel`` runs thunks on the orchestration pool; the ``agent`` leaves they
spawn run concurrently on the leaf pool. Real concurrency, unlike the old stub.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable, Optional

from . import router as router_mod
from .budget import Budget
from .ids import leaf_id as compute_leaf_id
from .ids import new_run_id, stable_hash
from .leaves import LeafRequest
from .paths import run_dir_for
from .scheduler import Engine


class Workflow:
    def __init__(self, engine: Engine, *, script_id: str):
        self._engine = engine
        self._script_id = script_id
        self._phase = "init"

    # -- phase / log -----------------------------------------------------
    def phase(self, name: str) -> None:
        self._phase = name
        self._engine.phase(name)

    def log(self, message: str, **fields: Any) -> None:
        self._engine.log(message, **fields)

    def notify(self, message: str, **fields: Any) -> None:
        """User-facing escalation (Telegram). Distinct from log() which stays
        in the journal — per the channel-scope doctrine, engine chatter must not
        reach the phone unless explicitly escalated."""
        self._engine.log(message, notify=True, **fields)

    # -- budget ----------------------------------------------------------
    def spend(self) -> dict:
        return self._engine.budget.spend()

    def remaining(self) -> dict:
        return self._engine.budget.remaining()

    def has_headroom(self, min_tokens: int = 1000) -> bool:
        return self._engine.budget.has_headroom(min_tokens=min_tokens)

    # -- leaves ----------------------------------------------------------
    def agent(
        self,
        prompt: str,
        *,
        label: Optional[str] = None,
        schema: Any = None,
        tier: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        toolsets: str = "",
        role: Optional[str] = None,
        backend: Optional[str] = None,
        needs: Optional[set] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        required: bool = True,
    ) -> Any:
        label = label or f"agent-{stable_hash(prompt, 8)}"
        needs = set(needs or [])
        if schema is not None:
            needs.add("structured")
        route = router_mod.choose(
            tier=tier, model=model, provider=provider, toolsets=toolsets,
            role=role, backend=backend, needs=needs,
        )
        lid = compute_leaf_id(
            run_script=self._script_id, phase=self._phase, label=label,
            prompt=prompt, model=route.model, backend=route.backend,
        )
        req = LeafRequest(
            leaf_id=lid, label=label, phase=self._phase, prompt=prompt, route=route,
            toolsets=toolsets, role=role, schema=schema, max_tokens=max_tokens,
            timeout=timeout,
        )
        res = self._engine.submit_leaf(req)
        if res.status != "completed":
            if required:
                raise RuntimeError(f"leaf {label} {res.status}: {res.error}")
            return None
        return res.value

    def local(
        self,
        fn: Callable[..., Any],
        *args: Any,
        label: Optional[str] = None,
        schema: Any = None,
        **kwargs: Any,
    ) -> Any:
        label = label or f"local-{getattr(fn, '__name__', 'fn')}-{stable_hash([args, kwargs], 6)}"
        route = router_mod.choose(tier="local", backend="local")
        lid = compute_leaf_id(
            run_script=self._script_id, phase=self._phase, label=label,
            prompt=stable_hash([getattr(fn, '__name__', 'fn'), args, kwargs]),
            model="local", backend="local",
        )
        req = LeafRequest(
            leaf_id=lid, label=label, phase=self._phase, prompt="<local>", route=route,
            schema=schema, local_fn=fn, local_args=args, local_kwargs=kwargs,
        )
        res = self._engine.submit_leaf(req)
        if res.status != "completed":
            raise RuntimeError(f"local leaf {label} {res.status}: {res.error}")
        return res.value

    # -- combinators -----------------------------------------------------
    def parallel(self, thunks: list[Callable[[], Any]]) -> list[Any]:
        futures = [self._engine.orch_pool.submit(t) for t in thunks]
        out = []
        for f in futures:
            try:
                out.append(f.result())
            except Exception as exc:
                self.log(f"parallel thunk failed: {exc}", level="warn")
                out.append(None)
        return out

    def pipeline(self, items: list[Any], *stages: Callable[..., Any]) -> list[Any]:
        def run_item(item, idx):
            cur = item
            for stage in stages:
                if cur is None:
                    break
                cur = stage(cur, item, idx)
            return cur

        futures = [self._engine.orch_pool.submit(run_item, it, i) for i, it in enumerate(items)]
        out = []
        for f in futures:
            try:
                out.append(f.result())
            except Exception as exc:
                self.log(f"pipeline item failed: {exc}", level="warn")
                out.append(None)
        return out

    def workflow(self, fn: Callable[["Workflow", Any], Any], inputs: Any = None,
                 *, label: str = "nested") -> Any:
        """Run a nested workflow as a leaf. Shares the same engine + pools —
        flat resource model, arbitrary logical nesting (no depth cap, unlike
        delegate_task's hard 3)."""
        saved = self._phase
        self.phase(f"{saved}/{label}")
        try:
            return fn(self, inputs)
        finally:
            self._phase = saved


def _load_script(script_path: str):
    spec = importlib.util.spec_from_file_location("hwf_user_script", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load script {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "run"):
        raise AttributeError(f"script {script_path} must define run(wf, args)")
    return mod


def run_workflow(
    *,
    run_fn: Optional[Callable[[Workflow, Any], Any]] = None,
    script_path: Optional[str] = None,
    args: Any = None,
    budget: Optional[dict] = None,
    run_id: Optional[str] = None,
    slug: str = "flowleaf",
    executor_kind: str = "thread",
    max_workers: Optional[int] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    script_id: Optional[str] = None,
) -> dict:
    """Top-level entrypoint. Resolves run dir, builds the engine, runs the
    script, writes the report. ``run_id`` set + matching run dir → resume."""
    if run_fn is None and script_path is None:
        raise ValueError("run_workflow needs run_fn or script_path")

    rid = run_id or new_run_id(slug)
    rdir = run_dir_for(rid)
    sid = script_id or (script_path or getattr(run_fn, "__qualname__", "inline"))

    mod = None
    if script_path is not None:
        mod = _load_script(script_path)
        run_fn = mod.run
        sid = script_id or f"{script_path}:{stable_hash(Path(script_path).read_text(encoding='utf-8'))}"

    manifest = {
        "run_id": rid, "script_id": sid, "args": args, "budget": budget,
        "executor_kind": executor_kind,
    }
    engine = Engine(
        run_dir=rdir, budget=Budget.from_spec(budget), executor_kind=executor_kind,
        max_workers=max_workers, manifest=manifest, on_event=on_event,
    )
    wf = Workflow(engine, script_id=sid)
    status = "completed"
    final = None
    error = None
    try:
        final = run_fn(wf, args)
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        engine.shutdown()
    report = engine.report(final=final, status=status)
    if error:
        report["error"] = error
    import json as _json
    Path(rdir / "run-report.json").write_text(
        _json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    return report
