"""wf.loop — bounded, resumable iterate-to-goal envelope (plan §1/§2, WS-B).

Each iteration runs ``step`` (executor) then ``verify`` (verifier) as ordinary
DAG leaves under iteration-scoped phases, so leaf-level cache/resume/budget
gating apply unchanged. The :class:`LoopLedger` extends the run journal with
iteration-level WAL events plus a materialized :class:`IterationRecord` per
finished iteration; a completed iteration never reruns after a crash.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from . import router as router_mod
from .ids import stable_hash

_SPEND_AXES = ("input_tokens", "output_tokens", "usd", "calls")


class VerifierIdentityError(RuntimeError):
    """The configured verifier resolves to the executor's (provider, model).
    A required gate must never self-grade (§4.2) — fails closed at loop start,
    never silently downgrades to same-brain grading."""


@dataclass(frozen=True)
class LoopSpec:
    goal: str
    max_iterations: int = 5
    budget: Optional[dict] = None              # loop-scoped ceiling (max_tokens/max_usd/max_calls)
    required_gates: Sequence[str] = ()         # gate ids; GateRunner lands in WS-D
    verifier_policy: Optional[dict] = None     # {"tier": ..., "must_differ_from_executor": bool}
    stop_conditions: Sequence[str] = ("goal_met", "no_progress", "budget_exhausted", "max_iterations")

    def identity(self) -> dict:
        return {
            "goal": self.goal,
            "max_iterations": self.max_iterations,
            "budget": self.budget,
            "required_gates": list(self.required_gates),
            "verifier_policy": self.verifier_policy,
            "stop_conditions": list(self.stop_conditions),
        }

    def spec_hash(self) -> str:
        return stable_hash(self.identity())


@dataclass
class IterationRecord:
    """One iteration's auditable trajectory: inputs, context hash, candidate,
    verifier verdict, gate results, leaves, repairs, failures, spend."""
    loop_id: str
    iteration: int
    status: str                # completed | failed
    context_hash: str
    inputs: dict
    candidate: Any
    verifier_verdict: Any
    gate_results: dict         # filled by GateRunner (WS-D)
    leaves: list
    artifact_refs: list
    repairs: int
    failures: list
    spend: dict
    elapsed_s: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class LoopRun:
    """The loop receipt — what ``wf.loop`` returns (as a dict) and what the
    Albedo boundary consumes (§10)."""
    loop_id: str
    goal: str
    spec_hash: str
    status: str                # completed | exhausted | cancelled | failed
    stop_reason: str
    iterations: int
    replayed: int
    candidate: Any
    spend: dict
    records: list

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class LoopLedger:
    """Iteration-level WAL events + materialized IterationRecords, layered on
    the run journal (extends the leaf WAL, never replaces it)."""

    def __init__(self, engine: Any, loop_id: str):
        self._engine = engine
        self._journal = engine.journal
        self.loop_id = loop_id

    def _emit(self, event: str, **fields: Any) -> None:
        self._engine._emit({"event": event, "loop_id": self.loop_id, **fields})

    def loop_started(self, spec: LoopSpec) -> None:
        self._emit("loop_started", goal=spec.goal, spec_hash=spec.spec_hash(),
                   max_iterations=spec.max_iterations)

    def iteration_started(self, n: int) -> None:
        self._emit("iteration_started", iteration=n)

    def iteration_replayed(self, n: int) -> None:
        self._emit("iteration_replayed", iteration=n)

    def iteration_finished(self, record: IterationRecord) -> None:
        ref = self._journal.write_iteration_record(
            f"{self.loop_id}-i{record.iteration}", record.as_dict())
        event = "iteration_completed" if record.status == "completed" else "iteration_failed"
        self._emit(event, iteration=record.iteration, record_ref=ref,
                   status=record.status, spend=record.spend, leaf_count=len(record.leaves))

    def loop_stopped(self, *, stop_reason: str, status: str, iterations: int,
                     replayed: int, spend: dict) -> None:
        self._emit("loop_stopped", stop_reason=stop_reason, status=status,
                   iterations=iterations, replayed=replayed, spend=spend)

    def verifier_identity_collision(self, verifier: Any, executor: Any) -> None:
        self._emit("verifier_identity_collision",
                   verifier={"provider": verifier.provider, "model": verifier.model},
                   executor={"provider": executor.provider, "model": executor.model})

    def completed_iterations(self) -> dict[int, dict]:
        return self._journal.completed_iterations(self.loop_id)


def evaluate_stop(spec: LoopSpec, *, iteration: int, records: list,
                  loop_spend: dict, exhausted: bool, cancelled: bool) -> Optional[str]:
    """Deterministic stop precedence (§7.2): goal_met > max_iterations >
    no_progress > budget_exhausted > cancelled. ``goal_met`` and ``no_progress``
    are WS-E hooks; ``max_iterations`` and ``budget_exhausted`` are hard bounds
    enforced regardless of ``spec.stop_conditions`` — a declared-bounded loop
    cannot be configured unbounded."""
    # goal_met: WS-E (GoalContract over gate results)
    if iteration >= spec.max_iterations:
        return "max_iterations"
    # no_progress: WS-E (FailureSignatureRegistry stall detector)
    if exhausted:
        return "budget_exhausted"
    if cancelled:
        return "cancelled"
    return None


def run_loop(wf: Any, *, spec: LoopSpec,
             step: Callable[[Any, dict], Any],
             verify: Optional[Callable[[Any, dict], Any]] = None) -> dict:
    engine = wf._engine
    entry_phase = wf._phase
    loop_id = "loop-" + stable_hash(
        {"script": wf._script_id, "phase": entry_phase, "spec": spec.identity()})
    ledger = LoopLedger(engine, loop_id)
    _enforce_verifier_identity(spec, ledger)
    completed = ledger.completed_iterations()
    ledger.loop_started(spec)

    tag = loop_id[5:11]
    records: list[dict] = []
    prev: Any = None
    replayed = 0
    loop_spend = {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "calls": 0}
    stop_reason = "max_iterations"

    for n in range(1, spec.max_iterations + 1):
        breached = False
        if n in completed:
            rec = completed[n]
            ledger.iteration_replayed(n)
            replayed += 1
        else:
            rec, breached = _run_iteration(
                wf, ledger=ledger, spec=spec, step=step, verify=verify,
                n=n, prev=prev, entry_phase=entry_phase, tag=tag)
        records.append(rec)
        if rec.get("status") == "completed":
            prev = rec.get("candidate")
        _accumulate(loop_spend, rec.get("spend") or {})
        exhausted = (breached or not engine.budget.has_headroom()
                     or _loop_budget_exhausted(spec.budget, loop_spend))
        reason = evaluate_stop(spec, iteration=n, records=records, loop_spend=loop_spend,
                               exhausted=exhausted, cancelled=engine._cancel.is_set())
        if reason:
            stop_reason = reason
            break

    if stop_reason in ("goal_met", "max_iterations") and records and records[-1]["status"] == "completed":
        status = "completed"
    elif stop_reason == "budget_exhausted":
        status = "exhausted"
    elif stop_reason == "cancelled":
        status = "cancelled"
    else:
        status = "failed"
    spend = {**loop_spend, "tokens": loop_spend["input_tokens"] + loop_spend["output_tokens"]}
    run = LoopRun(loop_id=loop_id, goal=spec.goal, spec_hash=spec.spec_hash(),
                  status=status, stop_reason=stop_reason, iterations=len(records),
                  replayed=replayed, candidate=prev, spend=spend, records=records)
    ledger.loop_stopped(stop_reason=stop_reason, status=status,
                        iterations=len(records), replayed=replayed, spend=spend)
    return run.as_dict()


def _run_iteration(wf: Any, *, ledger: LoopLedger, spec: LoopSpec,
                   step: Callable, verify: Optional[Callable],
                   n: int, prev: Any, entry_phase: str, tag: str) -> tuple[dict, bool]:
    engine = wf._engine
    ctx = {"goal": spec.goal, "iteration": n, "prev": prev}
    context_hash = stable_hash(ctx)
    ledger.iteration_started(n)
    spend_before = engine.budget.spend()
    with engine._all_lock:
        mark = len(engine._all)
    started = time.time()
    status = "completed"
    candidate: Any = None
    verdict: Any = None
    failures: list[dict] = []
    stage = "step"
    try:
        wf.phase(f"{entry_phase}/loop:{tag}/i{n}/step")
        candidate = step(wf, dict(ctx))
        if verify is not None:
            stage = "verify"
            wf.phase(f"{entry_phase}/loop:{tag}/i{n}/verify")
            verdict = verify(wf, {**ctx, "candidate": candidate})
    except Exception as exc:
        status = "failed"
        failures.append({"stage": stage, "error": str(exc)})

    spend_after = engine.budget.spend()
    with engine._all_lock:
        leaves = [dict(l) for l in engine._all[mark:]]
    delta = {k: spend_after[k] - spend_before[k] for k in _SPEND_AXES}
    breached = any(l.get("status") == "failed" and str(l.get("error") or "").startswith("budget:")
                   for l in leaves)
    failures.extend(
        {"stage": "leaf", "leaf_id": l.get("leaf_id"), "label": l.get("label"),
         "error": l.get("error"), "error_kind": l.get("error_kind")}
        for l in leaves if l.get("status") in ("failed", "schema_failed"))
    record = IterationRecord(
        loop_id=ledger.loop_id, iteration=n, status=status, context_hash=context_hash,
        inputs={"goal": spec.goal,
                "prev_hash": stable_hash(prev) if prev is not None else None},
        candidate=candidate, verifier_verdict=verdict, gate_results={},
        leaves=[_leaf_summary(l) for l in leaves],
        artifact_refs=[ref for l in leaves for ref in (l.get("artifact_refs") or [])],
        repairs=sum(int(l.get("repair_attempts") or 0) for l in leaves),
        failures=failures,
        spend={**delta, "tokens": delta["input_tokens"] + delta["output_tokens"]},
        elapsed_s=round(time.time() - started, 3),
    )
    ledger.iteration_finished(record)
    return record.as_dict(), breached


def _enforce_verifier_identity(spec: LoopSpec, ledger: LoopLedger) -> None:
    policy = spec.verifier_policy or {}
    if not policy.get("must_differ_from_executor"):
        return
    verifier = router_mod.choose(tier=policy.get("tier"))
    executor = router_mod.choose(tier=policy.get("executor_tier"))
    if (verifier.provider, verifier.model) == (executor.provider, executor.model):
        ledger.verifier_identity_collision(verifier, executor)
        raise VerifierIdentityError(
            f"verifier resolves to executor identity "
            f"({verifier.provider}, {verifier.model}); a required gate must not self-grade")


def _loop_budget_exhausted(budget: Optional[dict], spend: dict) -> bool:
    if not budget:
        return False
    tokens = spend["input_tokens"] + spend["output_tokens"]
    mt, mu, mc = budget.get("max_tokens"), budget.get("max_usd"), budget.get("max_calls")
    return ((mt is not None and tokens >= mt)
            or (mu is not None and spend["usd"] >= mu)
            or (mc is not None and spend["calls"] >= mc))


def _accumulate(total: dict, delta: dict) -> None:
    for k in _SPEND_AXES:
        total[k] += delta.get(k) or 0


def _leaf_summary(l: dict) -> dict:
    return {k: l.get(k) for k in (
        "leaf_id", "label", "phase", "status", "input_tokens", "output_tokens",
        "usd", "error", "error_kind", "repair_attempts", "cached_hit",
        "input_sha256", "input_chars", "block_refs")}
