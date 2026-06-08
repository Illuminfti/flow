"""The engine: two-tier concurrent scheduler with budget gate + crash-durable
journal.

Tier 1 — leaf pool: the real concurrency throttle (FLOW_MAX_WORKERS). Every unit
of model work runs here. Budget is gated and the journal written around each.

Tier 2 — orchestration pool: large + cheap. Runs the script's thunks / pipeline
items, which mostly block on leaf-pool futures. Separating the two is what
prevents pool-exhaustion deadlock when many thunks each await a leaf.

True concurrency (the audit's #1 flaw in the old stub): ``parallel`` submits
thunks to the orchestration pool; the leaves they spawn run concurrently on the
leaf pool. Wall-time of N one-second leaves with a pool of ≥N is ~1s, not N s —
asserted in the test suite.
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from . import cache as cache_mod
from . import config as config_mod
from . import schema as schema_mod
from .budget import Budget, BudgetExceeded, DeadlineExceeded
from .journal import Journal
from .leaves import LeafRequest, LeafResult, run_leaf


def _default_leaf_workers() -> int:
    env = os.environ.get("FLOW_MAX_WORKERS")
    if env:
        return max(1, int(env))
    return min(32, (os.cpu_count() or 4) * 4)


def _default_orch_workers() -> int:
    env = os.environ.get("FLOW_ORCH_WORKERS")
    if env:
        return max(2, int(env))
    return 256


class Engine:
    def __init__(
        self,
        *,
        run_dir: Path,
        budget: Optional[Budget] = None,
        max_workers: Optional[int] = None,
        orch_workers: Optional[int] = None,
        executor_kind: str = "thread",
        manifest: Optional[dict] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        self.run_dir = Path(run_dir)
        self.budget = budget or Budget()
        self.executor_kind = executor_kind
        self.max_workers = max_workers or _default_leaf_workers()
        self.orch_workers = orch_workers or _default_orch_workers()
        self.journal = Journal(self.run_dir).open(manifest=manifest)
        self._cache: dict[str, dict] = self.journal.completed_leaves()
        self._cache_lock = threading.Lock()
        self._on_event = on_event
        self._all: list[dict] = []
        self._all_lock = threading.Lock()
        self.resumed_count = len(self._cache)
        self._cancel = threading.Event()  # set by Ctrl+C / SIGTERM / deadline cascade

        if executor_kind == "process":
            # spawn, not fork: forking a multi-threaded parent (we hold thread
            # pools) risks child deadlocks.
            self._leaf_pool: Any = ProcessPoolExecutor(
                max_workers=self.max_workers, mp_context=mp.get_context("spawn"))
        else:
            self._leaf_pool = ThreadPoolExecutor(max_workers=self.max_workers,
                                                 thread_name_prefix="flow-leaf")
        self.orch_pool = ThreadPoolExecutor(max_workers=self.orch_workers,
                                            thread_name_prefix="flow-orch")

    # -- events ----------------------------------------------------------
    def _emit(self, record: dict) -> None:
        self.journal.append(record)
        if self._on_event is not None:
            try:
                self._on_event(record)
            except Exception:
                pass

    # -- leaf dispatch ---------------------------------------------------
    def submit_leaf(self, req: LeafRequest) -> LeafResult:
        """Run one leaf with cache/resume, budget gate, journal. Blocks the
        calling (orchestration) thread; concurrency comes from many callers."""
        # Resume / in-run dedup: identical leaf_id → reuse verbatim.
        with self._cache_lock:
            cached = self._cache.get(req.leaf_id)
        if cached is not None:
            # F1: a cache hit still counts against the deadline — otherwise a
            # run can serve stale results past its deadline.
            try:
                self.budget.check_deadline()
            except DeadlineExceeded as exc:
                res = _fail_result(req, f"deadline: {exc}")
                self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                            "event": "failed", "error": str(exc), "reason": "deadline"})
                self._record(res)
                return res
            self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                        "event": "cached", "backend": cached.get("backend")})
            res = _result_from_dict(cached)
            self._record(res, cached_hit=True)
            return res

        # Cancellation: stop spending the moment the run is cancelled (Ctrl+C,
        # deadline cascade). MUST write a result file — a terminal 'cancelled'
        # event without one is excluded from completed_leaves() and would re-run.
        if self._cancel.is_set():
            res = _cancelled_result(req)
            self.journal.write_leaf_result(req.leaf_id, res.as_dict())
            self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                        "event": "cancelled"})
            with self._cache_lock:
                self._cache[req.leaf_id] = res.as_dict()
            self._record(res)
            return res

        # Cross-run content cache (opt-in): reuse identical leaves across runs.
        cc = (config_mod.get().get("leaf") or {}).get("content_cache") or {}
        ck = None
        if cc.get("enabled") and req.route.backend != "local":
            ck = cache_mod.content_key(req.prompt, getattr(req, "schema_hash", ""),
                                       req.route.model, req.route.backend)
            hit = cache_mod.load(ck, cc.get("ttl_days"))
            if hit is not None:
                try:
                    self.budget.check_deadline()
                except DeadlineExceeded:
                    self._cancel.set()
                self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                            "event": "global_cached", "backend": hit.get("backend")})
                res = _result_from_dict(hit)
                with self._cache_lock:
                    self._cache[req.leaf_id] = res.as_dict()
                self._record(res, cached_hit=True)
                return res

        est_in = max(1, len(req.prompt) // 4)
        est_out = req.max_tokens or 400
        est_usd = req.route.est_usd(est_in, est_out)
        # F5: a schema leaf may make two model calls (initial + one repair); a
        # tool leaf runs a multi-turn loop — gate on the worst case, commit truth.
        mult = 1
        if req.route.backend != "local":
            if schema_mod.is_schema(req.schema):
                mult += 1
            if getattr(req, "tools", None):
                mult += 2

        try:
            self.budget.reserve(est_tokens=(est_in + est_out) * mult, est_usd=est_usd * mult)
        except (BudgetExceeded, DeadlineExceeded) as exc:
            reason = "deadline" if isinstance(exc, DeadlineExceeded) else "budget"
            if isinstance(exc, DeadlineExceeded):
                self._cancel.set()  # deadline breach → stop the whole fleet
            res = _fail_result(req, f"{reason}: {exc}")
            self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                        "event": "failed", "error": str(exc), "reason": reason})
            self._record(res)
            return res

        self._emit({
            "leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
            "event": "routed", "backend": req.route.backend,
            "provider": req.route.provider, "model": req.route.model,
            "why": req.route.why,
        })
        self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                    "event": "started", "backend": req.route.backend})

        timeout = self.budget.leaf_deadline(req.timeout)
        try:
            # F3: local leaves are pure Python with unpicklable closures — run
            # them in-thread (also faster: no IPC) even in process mode.
            if req.route.backend == "local":
                res: LeafResult = run_leaf(req)
            else:
                fut = self._leaf_pool.submit(run_leaf, req)
                res = fut.result(timeout=timeout)
        except Exception as exc:
            res = _fail_result(req, f"executor: {exc}")

        self.budget.commit(input_tokens=res.input_tokens,
                           output_tokens=res.output_tokens, usd=res.usd)

        res = self._artifact_leaf_output(req, res)
        result_ref = self.journal.write_leaf_result(req.leaf_id, res.as_dict())
        common = {
            "tokens": {"in": res.input_tokens, "out": res.output_tokens},
            "usd": res.usd, "elapsed_s": res.elapsed_s, "attempts": res.attempts,
            "tokens_estimated": res.tokens_estimated, "required": res.required,
            "error_kind": res.error_kind, "artifact_refs": res.artifact_refs,
            "result_ref": result_ref,
        }
        if res.status in ("failed", "schema_failed"):
            self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                        "event": res.status, "error": res.error, **common})
        else:
            self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                        "event": "completed", "backend": res.backend,
                        "provider": res.provider, "model": res.model,
                        "repaired": res.repaired, **common})
        with self._cache_lock:
            self._cache[req.leaf_id] = res.as_dict()
        if ck and res.status == "completed":
            cache_mod.store(ck, res.as_dict())
        self._record(res)
        return res

    def _record(self, res: LeafResult, cached_hit: bool = False) -> None:
        with self._all_lock:
            self._all.append({**res.as_dict(), "cached_hit": cached_hit})

    # -- phases / logs ---------------------------------------------------
    def phase(self, name: str) -> None:
        self._emit({"event": "phase", "phase": name})
        self.snapshot()

    def log(self, message: str, **fields: Any) -> None:
        self._emit({"event": "log", "message": message, **fields})

    def snapshot(self) -> None:
        self.journal.snapshot_state({
            "spend": self.budget.spend(),
            "remaining": self.budget.remaining(),
            "leaf_count": len(self._all),
            "resumed": self.resumed_count,
        })

    def _artifact_leaf_output(self, req: LeafRequest, res: LeafResult, *, limit: int = 12000) -> LeafResult:
        """Persist oversized raw leaf text before preview truncation.

        The materialized leaf result and run report should stay inspectable without
        hauling giant model/tool output through every downstream prompt or status
        view. The full text remains durable under artifacts/ with size + hash
        metadata attached to the node result.
        """
        text = res.text or ""
        if len(text) <= limit:
            return res
        artifacts_dir = self.run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        rel = f"artifacts/{req.leaf_id}-text.txt"
        path = self.run_dir / rel
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        preview = text[:limit]
        ref = {
            "kind": "leaf_text",
            "leaf_id": req.leaf_id,
            "label": req.label,
            "path": rel,
            "byte_count": len(text.encode("utf-8")),
            "char_count": len(text),
            "preview_char_count": len(preview),
            "truncation_reason": "oversized_leaf_text",
            "sha256": digest,
        }
        refs = list(res.artifact_refs or [])
        refs.append(ref)
        res.text = preview
        res.artifact_refs = refs
        return res

    # -- lifecycle -------------------------------------------------------
    def shutdown(self) -> None:
        try:
            self.orch_pool.shutdown(wait=True)
        finally:
            self._leaf_pool.shutdown(wait=True)
            self.snapshot()
            self.journal.close()

    def report(self, final: Any = None, status: str = "completed", finalization_error: str | None = None) -> dict:
        with self._all_lock:
            leaves = list(self._all)
        failed = [l for l in leaves if l["status"] in ("failed", "schema_failed")]
        required_failed = [l for l in failed if l.get("required", True)]
        warnings = [l for l in failed if not l.get("required", True)]
        effective_status = status
        if required_failed or (status == "failed" and not finalization_error):
            effective_status = "failed"
        elif finalization_error or warnings:
            effective_status = "completed_with_warnings"
        return {
            "status": effective_status,
            "run_dir": str(self.run_dir),
            "leaf_count": len(leaves),
            "resumed": self.resumed_count,
            "failed_count": len(failed),
            "required_failed_count": len(required_failed),
            "warning_count": len(warnings) + (1 if finalization_error else 0),
            "spend": self.budget.spend(),
            "remaining": self.budget.remaining(),
            "phases": sorted({l["phase"] for l in leaves if l.get("phase")}),
            "final": final,
            "finalization_error": finalization_error,
            "failed": [{"label": l["label"], "error": l["error"], "required": l.get("required", True),
                         "error_kind": l.get("error_kind"), "artifact_refs": l.get("artifact_refs", [])}
                        for l in failed],
            "required_failed": [{"label": l["label"], "error": l["error"], "required": True,
                                 "error_kind": l.get("error_kind"), "artifact_refs": l.get("artifact_refs", [])}
                                for l in required_failed],
            "warnings": [{"label": l["label"], "error": l["error"], "required": False,
                          "error_kind": l.get("error_kind"), "artifact_refs": l.get("artifact_refs", [])}
                         for l in warnings],
            "artifacts": [ref for l in leaves for ref in l.get("artifact_refs", [])],
        }


def _fail_result(req: LeafRequest, error: str) -> LeafResult:
    return LeafResult(
        leaf_id=req.leaf_id, label=req.label, phase=req.phase, status="failed",
        value=None, text="", backend=req.route.backend, provider=req.route.provider,
        model=req.route.model, input_tokens=0, output_tokens=0, usd=0.0,
        elapsed_s=0.0, pid=os.getpid(), error=error, required=req.required, error_kind="runtime_error",
    )


def _cancelled_result(req: LeafRequest) -> LeafResult:
    return LeafResult(
        leaf_id=req.leaf_id, label=req.label, phase=req.phase, status="cancelled",
        value=None, text="", backend=req.route.backend, provider=req.route.provider,
        model=req.route.model, input_tokens=0, output_tokens=0, usd=0.0,
        elapsed_s=0.0, pid=os.getpid(), error="cancelled", required=req.required, error_kind="cancelled",
    )


def _result_from_dict(d: dict) -> LeafResult:
    fields = {k: d.get(k) for k in LeafResult.__dataclass_fields__}
    return LeafResult(**fields)
