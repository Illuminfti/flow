"""The script-authoring API — what a workflow script calls.

A script is a Python module exposing ``run(wf, args)`` (and optional ``META``).
``wf`` is a :class:`Workflow` bound to an :class:`~flow.scheduler.Engine`.

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

import contextvars
import importlib.util
import inspect
import json
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from . import router as router_mod
from .budget import Budget
from .ids import leaf_id as compute_leaf_id
from .ids import new_run_id, stable_hash
from .leaves import LeafRequest
from .loop import LoopSpec, run_loop
from .paths import run_dir_for
from .scheduler import Engine


class FailureMode(str, Enum):
    LENIENT = "lenient"          # failed item -> None + warning (default, back-compat)
    FAIL_FAST = "fail_fast"      # raise ParallelError on first failure, cancel the rest
    COLLECT_ERRORS = "collect_errors"  # return an ExecutionResult envelope per item


@dataclass
class ExecutionResult:
    ok: bool
    value: Any
    error: Optional[str] = None
    index: int = -1


class ParallelError(RuntimeError):
    def __init__(self, index: int, cause: BaseException):
        super().__init__(f"item {index} failed: {cause}")
        self.index = index
        self.cause = cause


# Context-scoped current phase (see Workflow._phase) — concurrency-safe labelling.
_CURRENT_PHASE: contextvars.ContextVar[str] = contextvars.ContextVar("flow_phase", default="init")


class Workflow:
    def __init__(self, engine: Engine, *, script_id: str):
        self._engine = engine
        self._script_id = script_id

    @property
    def _phase(self) -> str:
        # Context-scoped, not a shared attribute: parallel/pipeline thunks each
        # capture the phase active at submission, so concurrent branches can't
        # clobber each other's phase label.
        return _CURRENT_PHASE.get()

    # -- phase / log -----------------------------------------------------
    def phase(self, name: str) -> None:
        _CURRENT_PHASE.set(name)
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

    def cancelled(self) -> bool:
        """True once the run is cancelled (Ctrl+C / SIGTERM / deadline breach).
        Scripts can check this to return a partial result gracefully."""
        return self._engine._cancel.is_set()

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
        backend: Optional[str] = None,
        needs: Optional[set] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        tools: Optional[list] = None,
        tool_approval_gates: Optional[dict] = None,
        required: bool = True,
    ) -> Any:
        label = label or f"agent-{stable_hash(prompt, 8)}"
        needs = set(needs or [])
        if schema is not None:
            needs.add("structured")
        if tools:
            needs.add("tool_call")  # only route to tool-capable models
        route = router_mod.choose(
            tier=tier, model=model, provider=provider, toolsets=toolsets,
            backend=backend, needs=needs,
        )
        schema_hash = stable_hash(schema) if schema is not None else ""
        tools_identity = ""
        if tools:
            tools_identity = stable_hash({
                "tools": [
                    getattr(t, "name", None)
                    or getattr(t, "__name__", None)
                    or f"{getattr(t, '__module__', '')}.{getattr(t, '__qualname__', repr(t))}"
                    for t in tools
                ],
                "approval": tool_approval_gates or {},
            })
        lid = compute_leaf_id(
            run_script=self._script_id, phase=self._phase, label=label,
            prompt=prompt, model=route.model, backend=route.backend, schema=schema_hash,
            provider=provider or "", toolsets=toolsets or "", tools=tools_identity,
            max_tokens=str(max_tokens) if max_tokens is not None else "",
        )
        req = LeafRequest(
            leaf_id=lid, label=label, phase=self._phase, prompt=prompt, route=route,
            toolsets=toolsets, schema=schema, schema_hash=schema_hash, max_tokens=max_tokens,
            timeout=timeout, tools=list(tools or []), tool_approval_gates=dict(tool_approval_gates or {}),
            required=required,
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
        required: bool = True,
        **kwargs: Any,
    ) -> Any:
        label = label or f"local-{getattr(fn, '__name__', 'fn')}-{stable_hash([args, kwargs], 6)}"
        route = router_mod.choose(tier="local", backend="local")
        lid = compute_leaf_id(
            run_script=self._script_id, phase=self._phase, label=label,
            prompt=stable_hash([getattr(fn, '__name__', 'fn'), args, kwargs]),
            model="local", backend="local",
            schema=stable_hash(schema) if schema is not None else "",
        )
        req = LeafRequest(
            leaf_id=lid, label=label, phase=self._phase, prompt="<local>", route=route,
            schema=schema, local_fn=fn, local_args=args, local_kwargs=kwargs, required=required,
        )
        res = self._engine.submit_leaf(req)
        if res.status != "completed":
            if required:
                raise RuntimeError(f"local leaf {label} {res.status}: {res.error}")
            return None
        return res.value

    # -- loop (v2) ---------------------------------------------------------
    def loop(self, *, spec: LoopSpec, step: Callable[["Workflow", dict], Any],
             verify: Optional[Callable[["Workflow", dict], Any]] = None) -> dict:
        """Bounded iterate-to-goal envelope (plan §1/§8). Each iteration runs
        ``step`` then ``verify`` as ordinary leaves; returns the LoopRun receipt."""
        token = _CURRENT_PHASE.set(_CURRENT_PHASE.get())
        try:
            return run_loop(self, spec=spec, step=step, verify=verify)
        finally:
            _CURRENT_PHASE.reset(token)

    # -- combinators -----------------------------------------------------
    def _bind_phase(self, fn: Callable) -> Callable:
        """Wrap a thunk so it runs in the pool thread with the phase that was
        active when it was submitted (phase scoping under concurrency)."""
        phase = _CURRENT_PHASE.get()

        def runner(*a, **k):
            _CURRENT_PHASE.set(phase)
            return fn(*a, **k)
        return runner

    def _harvest(self, futures: list, mode: str) -> list[Any]:
        """Collect futures with the chosen failure mode + cancellation.

        mode: "lenient" (failed -> None + warn, default), "fail_fast" (raise
        ParallelError, cancel the rest), "collect_errors" (ExecutionResult envelopes).
        """
        if mode == "fail_fast":
            out: list[Any] = [None] * len(futures)
            pending = set(futures)
            index = {f: i for i, f in enumerate(futures)}
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in done:
                    idx = index[f]
                    if self._engine._cancel.is_set():
                        for p in pending:
                            p.cancel()
                        return out
                    try:
                        out[idx] = f.result()
                    except Exception as exc:
                        for p in pending:
                            p.cancel()
                        raise ParallelError(idx, exc) from exc
            return out

        out: list[Any] = []
        for idx, f in enumerate(futures):
            if self._engine._cancel.is_set():
                for p in futures[idx:]:
                    p.cancel()
                break
            try:
                r = f.result()
                out.append(ExecutionResult(True, r, None, idx) if mode == "collect_errors" else r)
            except Exception as exc:
                if mode == "collect_errors":
                    out.append(ExecutionResult(False, None, str(exc), idx))
                else:
                    self.log(f"item {idx} failed: {exc}", level="warn")
                    out.append(None)
        return out

    def _stage_call(self, stage: Callable[..., Any], cur: Any, item: Any, idx: int) -> Any:
        """Call a pipeline stage with ergonomic arity while preserving the old
        three-argument contract for normal and uninspectable callables."""
        try:
            sig = inspect.signature(stage)
        except (TypeError, ValueError):
            return stage(cur, item, idx)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        required = [p for p in positional if p.default is p.empty]
        if any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()):
            return stage(cur, item, idx)
        if len(required) > 3:
            raise TypeError(
                f"pipeline stage {getattr(stage, '__name__', stage)!r} requires "
                f"{len(required)} positional arguments; supported arities are 1, 2, or 3"
            )
        argc = min(3, len(positional))
        if argc < 1:
            raise TypeError(
                f"pipeline stage {getattr(stage, '__name__', stage)!r} must accept "
                "at least one positional argument"
            )
        args = (cur, item, idx)[:argc]
        return stage(*args)

    def parallel(self, thunks: list[Callable[[], Any]], *, mode: str = "lenient") -> list[Any]:
        futures = [self._engine.orch_pool.submit(self._bind_phase(t)) for t in thunks]
        return self._harvest(futures, mode)

    def pipeline(self, items: list[Any], *stages: Callable[..., Any], mode: str = "lenient") -> list[Any]:
        def run_item(item, idx):
            cur = item
            for stage in stages:
                if cur is None:
                    break
                cur = self._stage_call(stage, cur, item, idx)
            return cur

        futures = [self._engine.orch_pool.submit(self._bind_phase(run_item), it, i)
                   for i, it in enumerate(items)]
        return self._harvest(futures, mode)

    def workflow(self, fn: Callable[["Workflow", Any], Any], inputs: Any = None,
                 *, label: str = "nested") -> Any:
        """Run a nested workflow as a leaf. Shares the same engine + pools —
        flat resource model, arbitrary logical nesting (no depth cap, unlike
        delegate_task's hard 3)."""
        saved = _CURRENT_PHASE.get()
        token = _CURRENT_PHASE.set(f"{saved}/{label}")
        self._engine.phase(f"{saved}/{label}")
        try:
            return fn(self, inputs)
        finally:
            _CURRENT_PHASE.reset(token)


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
    slug: str = "flow",
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
    # Resume: rehydrate args/budget from the persisted manifest when the caller
    # omits them. A resume that re-runs the script with args=None crashes it,
    # and a caller-drifted budget spec silently re-applies a different ceiling
    # (observed: tool-202625-5382 resumed with 800000 against a 420000 manifest).
    manifest_path = rdir / "manifest.json"
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if args is None:
            args = prior.get("args")
        if budget is None:
            budget = prior.get("budget")
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
    # Ctrl+C / SIGTERM → cancel the run (cooperative). Off-main-thread (pytest,
    # nested) signal.signal raises ValueError — guard and skip, never crash.
    import signal
    _prev = {}
    for _s in (signal.SIGINT, signal.SIGTERM):
        try:
            _prev[_s] = signal.signal(_s, lambda *_a: engine._cancel.set())
        except (ValueError, OSError):
            pass
    finalization_error = None
    try:
        final = run_fn(wf, args)
    except Exception as exc:
        error = str(exc)
        with engine._all_lock:
            leaves = list(engine._all)
        required_failed = [l for l in leaves if l.get("status") in ("failed", "schema_failed") and l.get("required", True)]
        completed = [l for l in leaves if l.get("status") == "completed"]
        if completed and not required_failed:
            status = "completed"
            finalization_error = str(exc)
            final = {
                "fallback": "finalization_failed_after_completed_leaves",
                "error": str(exc),
                "completed_leaf_count": len(completed),
                "artifacts": [ref for l in leaves for ref in l.get("artifact_refs", [])],
                "retry_hint": "Resume this run after reducing final synthesis input or raising the budget.",
            }
        else:
            status = "failed"
    finally:
        for _s, _h in _prev.items():
            try:
                signal.signal(_s, _h)
            except (ValueError, OSError):
                pass
        engine.shutdown()
    report = engine.report(final=final, status=status, finalization_error=finalization_error)
    if error:
        report["error"] = error
    Path(rdir / "run-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    return report
