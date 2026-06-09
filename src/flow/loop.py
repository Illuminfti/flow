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
from .gates import GateRunner, GoalContract, default_goal_contract
from .handoff import build_handoff
from .ids import stable_hash
from .signatures import (FailureSignature, RepairRouter, is_stalled,
                         signatures_from_record)

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
    required_gates: Sequence[str] = ()         # gate ids run by GateRunner each iteration (WS-D)
    verifier_policy: Optional[dict] = None     # {"tier": ..., "must_differ_from_executor": bool}
    stop_conditions: Sequence[str] = ("goal_met", "no_progress", "budget_exhausted", "max_iterations")
    stall_limit: int = 2                       # consecutive evidence-identical iterations → no_progress
    goal_contract: Optional[GoalContract] = None  # default: all required gates pass (§5.1)
    max_repairs_per_iteration: int = 1         # bounded RepairRouter retries (WS-E)

    def contract(self) -> Optional[GoalContract]:
        if self.goal_contract is not None:
            return self.goal_contract
        if self.required_gates:
            return default_goal_contract(self.required_gates)
        return None  # no gates, no contract → goal_met can never vacuously fire

    def identity(self) -> dict:
        return {
            "goal": self.goal,
            "max_iterations": self.max_iterations,
            "budget": self.budget,
            "required_gates": list(self.required_gates),
            "verifier_policy": self.verifier_policy,
            "stop_conditions": list(self.stop_conditions),
            "stall_limit": self.stall_limit,
            "goal_contract": (self.goal_contract.description
                              if self.goal_contract is not None else None),
            "max_repairs_per_iteration": self.max_repairs_per_iteration,
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
    status: str                # completed | exhausted | stalled | cancelled | failed
    stop_reason: str
    goal_met: bool
    iterations: int
    replayed: int
    candidate: Any
    spend: dict
    records: list
    handoff: dict

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

    def failure_signature(self, iteration: int, sig: "FailureSignature") -> None:
        self._emit("failure_signature", iteration=iteration, **sig.as_dict())

    def handoff_written(self, path: str, *, stop_reason: str,
                        next_bounded_action: str) -> None:
        self._emit("handoff_written", path=path, stop_reason=stop_reason,
                   next_bounded_action=next_bounded_action)

    def verifier_identity_collision(self, verifier: Any, executor: Any) -> None:
        self._emit("verifier_identity_collision",
                   verifier={"provider": verifier.provider, "model": verifier.model},
                   executor={"provider": executor.provider, "model": executor.model})

    def completed_iterations(self) -> dict[int, dict]:
        return self._journal.completed_iterations(self.loop_id)


def evaluate_stop(spec: LoopSpec, *, iteration: int, records: list,
                  loop_spend: dict, exhausted: bool, cancelled: bool) -> Optional[str]:
    """Deterministic stop precedence (§7.2): goal_met > max_iterations >
    no_progress > budget_exhausted > cancelled. A pure function of the
    iteration records — the same journal replays to the same ``stop_reason``
    at the same iteration (invariant I3). ``max_iterations`` and
    ``budget_exhausted`` are hard bounds enforced regardless of
    ``spec.stop_conditions`` — a declared-bounded loop cannot be configured
    unbounded."""
    contract = spec.contract()
    if ("goal_met" in spec.stop_conditions and contract is not None
            and records and contract.satisfied(records[-1])):
        return "goal_met"
    if iteration >= spec.max_iterations:
        return "max_iterations"
    if ("no_progress" in spec.stop_conditions
            and is_stalled(records, stall_limit=spec.stall_limit)):
        return "no_progress"
    if exhausted:
        return "budget_exhausted"
    if cancelled:
        return "cancelled"
    return None


def run_loop(wf: Any, *, spec: LoopSpec,
             step: Callable[[Any, dict], Any],
             verify: Optional[Callable[[Any, dict], Any]] = None,
             gate_runner: Optional[GateRunner] = None) -> dict:
    engine = wf._engine
    entry_phase = wf._phase
    loop_id = "loop-" + stable_hash(
        {"script": wf._script_id, "phase": entry_phase, "spec": spec.identity()})
    ledger = LoopLedger(engine, loop_id)
    _enforce_verifier_identity(spec, ledger)
    completed = ledger.completed_iterations()
    ledger.loop_started(spec)

    tag = loop_id[5:11]
    runner = gate_runner or GateRunner()
    repair_router = RepairRouter()
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
                n=n, prev=prev, prev_record=records[-1] if records else None,
                entry_phase=entry_phase, tag=tag,
                runner=runner, repair_router=repair_router)
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

    goal_met = stop_reason == "goal_met"
    contract = spec.contract()
    if goal_met:
        status = "completed"
    elif stop_reason == "max_iterations":
        # With a contract declared, hitting the cap without meeting the goal is
        # exhaustion, not success; a plain bounded loop keeps v1 semantics.
        if contract is not None:
            status = "exhausted"
        else:
            status = ("completed" if records and records[-1]["status"] == "completed"
                      else "failed")
    elif stop_reason == "no_progress":
        status = "stalled"
    elif stop_reason == "budget_exhausted":
        status = "exhausted"
    elif stop_reason == "cancelled":
        status = "cancelled"
    else:
        status = "failed"
    spend = {**loop_spend, "tokens": loop_spend["input_tokens"] + loop_spend["output_tokens"]}

    run_id = engine.run_dir.name
    handoff = build_handoff(
        run_id=run_id, loop_id=loop_id, spec=spec, status=status,
        stop_reason=stop_reason, goal_met=goal_met, records=records,
        replayed=replayed, spend=spend,
        budget_remaining=engine.budget.remaining())
    handoff_ref = engine.journal.write_handoff(handoff.as_dict())
    ledger.handoff_written(handoff_ref, stop_reason=stop_reason,
                           next_bounded_action=handoff.next_bounded_action)

    run = LoopRun(loop_id=loop_id, goal=spec.goal, spec_hash=spec.spec_hash(),
                  status=status, stop_reason=stop_reason, goal_met=goal_met,
                  iterations=len(records), replayed=replayed, candidate=prev,
                  spend=spend, records=records, handoff=handoff.as_dict())
    ledger.loop_stopped(stop_reason=stop_reason, status=status,
                        iterations=len(records), replayed=replayed, spend=spend)
    return run.as_dict()


def _run_iteration(wf: Any, *, ledger: LoopLedger, spec: LoopSpec,
                   step: Callable, verify: Optional[Callable],
                   n: int, prev: Any, prev_record: Optional[dict],
                   entry_phase: str, tag: str,
                   runner: GateRunner, repair_router: RepairRouter) -> tuple[dict, bool]:
    engine = wf._engine
    ctx = {"goal": spec.goal, "iteration": n, "prev": prev,
           "prev_gates": (prev_record or {}).get("gate_results") or {},
           "prev_failures": (prev_record or {}).get("failures") or []}
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
    repairs = 0
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

    gate_ctx = {"candidate": candidate, "verdict": verdict, "spec": spec,
                "iteration": n}
    gate_results: dict[str, dict] = {}
    if spec.required_gates and status == "completed":
        with engine._all_lock:
            gate_ctx["leaves"] = [dict(l) for l in engine._all[mark:]]
        gate_results = runner.run(spec.required_gates, gate_ctx)
        # Bounded repair: a malformed verifier verdict gets one re-ask with the
        # parse error attached (RepairRouter strategy "verifier_repair").
        while (repairs < spec.max_repairs_per_iteration and verify is not None
               and _verifier_repair_needed(gate_results, repair_router)):
            repairs += 1
            err = gate_results["verifier"].get("error")
            try:
                wf.phase(f"{entry_phase}/loop:{tag}/i{n}/verify-repair{repairs}")
                verdict = verify(wf, {**ctx, "candidate": candidate,
                                      "repair": err, "previous": verdict})
            except Exception as exc:
                failures.append({"stage": "verify_repair", "error": str(exc)})
                break
            gate_ctx["verdict"] = verdict
            with engine._all_lock:
                gate_ctx["leaves"] = [dict(l) for l in engine._all[mark:]]
            gate_results = runner.run(spec.required_gates, gate_ctx)

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
        candidate=candidate, verifier_verdict=verdict, gate_results=gate_results,
        leaves=[_leaf_summary(l) for l in leaves],
        artifact_refs=[ref for l in leaves for ref in (l.get("artifact_refs") or [])],
        repairs=repairs + sum(int(l.get("repair_attempts") or 0) for l in leaves),
        failures=failures,
        spend={**delta, "tokens": delta["input_tokens"] + delta["output_tokens"]},
        elapsed_s=round(time.time() - started, 3),
    )
    rec_dict = record.as_dict()
    for sig in signatures_from_record(rec_dict):
        ledger.failure_signature(n, sig)
    ledger.iteration_finished(record)
    return rec_dict, breached


def _verifier_repair_needed(gate_results: dict, router: RepairRouter) -> bool:
    g = gate_results.get("verifier")
    if not g or g.get("passed") or g.get("verdict") != "error":
        return False
    sig = FailureSignature(error_kind=str(g.get("error_kind") or "runtime_error"),
                           message=str(g.get("error") or ""), gate_id="verifier")
    return router.route(sig) == "verifier_repair"


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
