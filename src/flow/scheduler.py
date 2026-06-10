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

from . import blocks as blocks_mod
from . import cache as cache_mod
from . import config as config_mod
from . import schema as schema_mod
from . import worktree as worktree_mod
from .backends.pricing import estimate_tokens
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
        self.blocks = blocks_mod.BlockStore(self.run_dir)
        self._charged_blocks: set[tuple[str, str]] = set()
        self._blocks_lock = threading.Lock()
        # Replay committed spend so the ceiling holds across resumes (not
        # N× reset). Runs pre-pool, single-threaded — no _lock needed.
        # Spend.tokens is a read-only @property; mutate the axes directly.
        spend = self.budget._spend
        for r in self._cache.values():
            spend.input_tokens += int(r.get("input_tokens") or 0)
            spend.output_tokens += int(r.get("output_tokens") or 0)
            spend.usd += float(r.get("usd") or 0.0)
            spend.calls += 1
        # Block charges are committed truth too (§11.4): replay them into spend
        # and into the charged set so a rerun iteration never double-charges.
        replayed_blocks = 0
        for rec in self.journal.replay():
            if rec.get("event") == "block_charged":
                self._charged_blocks.add((rec["scope"], rec["sha256"]))
                spend.input_tokens += int(rec.get("input_tokens") or 0)
                replayed_blocks += 1
        if self._cache or replayed_blocks:
            self.journal.append({"event": "budget_replay", "leaves": len(self._cache),
                                 "blocks": replayed_blocks,
                                 "replayed_spend": spend.as_dict()})
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

        self._charge_blocks(req)
        est_in = max(1, len(req.prompt) // 4)
        est_out = req.max_tokens or 400
        if getattr(req, "reserve_tokens", 0):
            # Agent leaves ingest far more than their prompt (repo context,
            # tool output) — honor the explicit reserve hint as the floor.
            est_in = max(est_in, int(req.reserve_tokens) - est_out)
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
        wt_path = None
        wt_base = ""
        orig_workspace = req.workspace
        if req.isolation == "worktree" and req.route.backend == "agent_cli":
            # Each isolated agent leaf runs in its own detached worktree; the
            # change set comes back as a patch artifact, never a live mutation.
            try:
                wt_path, wt_base = worktree_mod.prepare(
                    req.workspace, self.run_dir / "worktrees" / req.leaf_id[:16])
                req.workspace = str(wt_path)
            except Exception as exc:
                res = _fail_result(req, f"worktree: {exc}")
                self._emit({"leaf_id": req.leaf_id, "label": req.label, "phase": req.phase,
                            "event": "failed", "error": str(exc), "reason": "worktree"})
                self._record(res)
                return res
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
        if wt_path is not None:
            patch_file = self.run_dir / "artifacts" / f"{req.leaf_id[:16]}.patch"
            evidence = worktree_mod.finalize(orig_workspace, wt_path, patch_file,
                                             base_sha=wt_base)
            if evidence.get("patch"):
                evidence["patch"] = str(Path(evidence["patch"]).relative_to(self.run_dir))
                res.artifact_refs = list(res.artifact_refs) + [evidence["patch"]]
            res.patch = evidence

        # Lever-1 measurement infra: persist what each leaf actually ingested,
        # so the dedup rate is measured, not inferred from input-token floors.
        res.input_sha256 = hashlib.sha256(req.prompt.encode("utf-8")).hexdigest()
        res.input_chars = len(req.prompt)
        res.block_refs = blocks_mod.refs_in(req.prompt)

        self.budget.commit(input_tokens=res.input_tokens,
                           output_tokens=res.output_tokens, usd=res.usd)

        res = self._artifact_leaf_output(req, res)
        result_ref = self.journal.write_leaf_result(req.leaf_id, res.as_dict())
        common = {
            "tokens": {"in": res.input_tokens, "out": res.output_tokens},
            "usd": res.usd, "elapsed_s": res.elapsed_s, "attempts": res.attempts,
            "tokens_estimated": res.tokens_estimated, "required": res.required,
            "repair_attempts": res.repair_attempts, "self_healed": res.self_healed,
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

    def _charge_blocks(self, req: LeafRequest) -> None:
        """Charge each referenced shared block once per iteration scope (§6 L2),
        not once per sibling. WAL-first: the charge is journaled before it is
        committed, so a crash-resume replays committed truth exactly once."""
        scope = blocks_mod.block_scope(req.phase)
        for sha in blocks_mod.refs_in(req.prompt):
            with self._blocks_lock:
                if (scope, sha) in self._charged_blocks:
                    continue
                self._charged_blocks.add((scope, sha))
            tokens = estimate_tokens(self.blocks.read(sha))
            self._emit({"event": "block_charged", "scope": scope, "sha256": sha,
                        "input_tokens": tokens})
            self.budget.commit(input_tokens=tokens, output_tokens=0, usd=0.0)

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
        self_healed = [l for l in leaves if l.get("self_healed")]
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
            "self_healed_count": len(self_healed),
            "warning_count": len(warnings) + (1 if finalization_error else 0),
            "spend": self.budget.spend(),
            "remaining": self.budget.remaining(),
            "phases": sorted({l["phase"] for l in leaves if l.get("phase")}),
            "final": final,
            "finalization_error": finalization_error,
            "failed": [{"label": l["label"], "error": l["error"], "required": l.get("required", True),
                         "error_kind": l.get("error_kind"), "repair_attempts": l.get("repair_attempts", 0),
                         "artifact_refs": l.get("artifact_refs", [])}
                        for l in failed],
            "required_failed": [{"label": l["label"], "error": l["error"], "required": True,
                                 "error_kind": l.get("error_kind"), "repair_attempts": l.get("repair_attempts", 0),
                                 "artifact_refs": l.get("artifact_refs", [])}
                                for l in required_failed],
            "warnings": [{"label": l["label"], "error": l["error"], "required": False,
                          "error_kind": l.get("error_kind"), "repair_attempts": l.get("repair_attempts", 0),
                          "artifact_refs": l.get("artifact_refs", [])}
                         for l in warnings],
            "self_healed": [{"label": l["label"], "phase": l.get("phase"),
                             "repair_attempts": l.get("repair_attempts", 0),
                             "provider": l.get("provider"), "model": l.get("model")}
                            for l in self_healed],
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
