"""HandoffReport — what a stopped loop hands the orchestrator (plan §10, WS-G).

Every loop stop (goal met, exhausted, stalled, cancelled, failed) produces a
handoff with a *bounded* next action, a verified/rejected iteration split, and
the top recurring failure signatures — so the consumer (Albedo) decides what
to do next from receipts, never from a bare ``failed`` status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .signatures import FailureSignatureRegistry


@dataclass
class HandoffReport:
    loop_id: str
    run_id: str
    goal: str
    status: str
    stop_reason: str
    goal_met: bool
    iterations: int
    replayed: int
    verified: list = field(default_factory=list)    # iterations whose gates all passed
    rejected: list = field(default_factory=list)    # iterations with a failed/skipped gate
    failure_signatures: list = field(default_factory=list)
    spend: dict = field(default_factory=dict)
    budget_remaining: Optional[dict] = None
    next_bounded_action: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _split_iterations(records: list[dict]) -> tuple[list[dict], list[dict]]:
    verified, rejected = [], []
    for rec in records:
        gates = rec.get("gate_results") or {}
        entry = {"iteration": rec.get("iteration"),
                 "status": rec.get("status"),
                 "gates": {gid: bool(g.get("passed")) for gid, g in gates.items()}}
        ok = rec.get("status") == "completed" and gates and all(
            g.get("passed") for g in gates.values())
        (verified if ok else rejected).append(entry)
    return verified, rejected


def _next_action(*, stop_reason: str, goal_met: bool, run_id: str,
                 registry: FailureSignatureRegistry, spec: Any) -> str:
    top = registry.top(1)
    top_desc = (f"{top[0]['gate_id'] or top[0]['error_kind']}: {top[0]['message']}"
                if top else "")
    if goal_met:
        return "none — goal met; consume the accepted candidate"
    if stop_reason == "budget_exhausted":
        return (f"raise the loop/run token ceiling and `flow resume {run_id}` — "
                f"completed iterations replay free")
    if stop_reason == "no_progress":
        return (f"break the stall before resuming: identical evidence across the last "
                f"{getattr(spec, 'stall_limit', 2)} iterations"
                + (f" (recurring failure — {top_desc})" if top_desc else ""))
    if stop_reason == "cancelled":
        return f"`flow resume {run_id}` to finish the remaining iterations"
    if stop_reason == "max_iterations":
        return ("raise max_iterations or revise the goal contract, then "
                f"`flow resume {run_id}` — completed iterations replay free")
    return (f"fix the recurring failure ({top_desc}) and `flow resume {run_id}`"
            if top_desc else f"inspect the failure records, then `flow resume {run_id}`")


def build_handoff(*, run_id: str, loop_id: str, spec: Any, status: str,
                  stop_reason: str, goal_met: bool, records: list[dict],
                  replayed: int, spend: dict,
                  budget_remaining: Optional[dict] = None) -> HandoffReport:
    registry = FailureSignatureRegistry.from_records(records)
    verified, rejected = _split_iterations(records)
    return HandoffReport(
        loop_id=loop_id, run_id=run_id, goal=spec.goal, status=status,
        stop_reason=stop_reason, goal_met=goal_met, iterations=len(records),
        replayed=replayed, verified=verified, rejected=rejected,
        failure_signatures=registry.top(5), spend=spend,
        budget_remaining=budget_remaining,
        next_bounded_action=_next_action(stop_reason=stop_reason, goal_met=goal_met,
                                         run_id=run_id, registry=registry, spec=spec))
