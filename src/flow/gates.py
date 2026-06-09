"""Acceptance gates + goal contracts for ``wf.loop`` (plan §5.1/§6 L3, WS-D).

Gates are tiered (Lever 3): deterministic checks run first and cost nothing;
verifier-tier (LLM) gates are skipped when a required deterministic gate has
already failed, so tokens are never spent grading a candidate that cannot be
accepted. A :class:`GoalContract` is a boolean predicate over one iteration's
gate results — P0 keeps it boolean-over-gates (no semantic scoring, §9).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import router as router_mod
from . import schema as schema_mod

DETERMINISTIC = "deterministic"
VERIFIER = "verifier"

# Gate fn contract: (ctx: dict) -> GateResult. ctx carries iteration state:
#   candidate, verdict (raw verify output), leaves, spec, iteration.
GateFn = Callable[[dict], "GateResult"]


@dataclass
class GateResult:
    gate_id: str
    passed: bool
    tier: str = DETERMINISTIC
    verdict: str = ""            # accept | reject | skipped | missing | error
    issues: list = field(default_factory=list)
    identity: Optional[dict] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _gate_schema(ctx: dict) -> GateResult:
    """Deterministic: every leaf in the iteration completed and none failed
    schema validation. Leaf-level enforcement already ran (schema.py); this
    lifts it to an iteration-level accept/reject."""
    bad = [l for l in ctx.get("leaves", ())
           if l.get("status") in ("failed", "schema_failed")]
    return GateResult(
        gate_id="schema", passed=not bad, tier=DETERMINISTIC,
        verdict="accept" if not bad else "reject",
        issues=[{"leaf_id": l.get("leaf_id"), "error": l.get("error"),
                 "error_kind": l.get("error_kind")} for l in bad])


def _gate_artifact(ctx: dict) -> GateResult:
    """Deterministic: the step produced a candidate at all."""
    ok = ctx.get("candidate") is not None
    return GateResult(gate_id="artifact", passed=ok, tier=DETERMINISTIC,
                      verdict="accept" if ok else "reject",
                      issues=[] if ok else [{"error": "step produced no candidate"}])


def normalize_verdict(raw: Any) -> tuple[Optional[dict], Optional[str]]:
    """Coerce a verifier's output into ``{"verdict": ..., "issues": [...]}``.
    Returns (normalized, error). A malformed verdict returns (None, error) so
    the RepairRouter can select ``verifier_repair`` (WS-E)."""
    if raw is None:
        return None, "verifier returned nothing"
    if isinstance(raw, dict):
        if "verdict" in raw:
            return {"verdict": str(raw["verdict"]).lower(),
                    "issues": list(raw.get("issues") or [])}, None
        return None, f"verifier dict missing 'verdict' key: {sorted(raw)}"
    if isinstance(raw, str):
        ok, value, err = schema_mod.parse(raw, {"type": "object", "required": ["verdict"]})
        if ok and isinstance(value, dict):
            return {"verdict": str(value["verdict"]).lower(),
                    "issues": list(value.get("issues") or [])}, None
        return None, err or "verifier output is not a verdict object"
    return None, f"verifier returned unsupported type {type(raw).__name__}"


def _gate_verifier(ctx: dict) -> GateResult:
    """Verifier-tier: the (identity-distinct, §4) verifier accepted the
    candidate with no unresolved adversarial findings (§5.1)."""
    spec = ctx.get("spec")
    policy = (getattr(spec, "verifier_policy", None) or {}) if spec else {}
    identity = None
    tier = policy.get("tier")
    if tier:
        try:
            route = router_mod.choose(tier=tier)
            identity = {"provider": route.provider, "model": route.model}
        except Exception:
            identity = None
    normalized, err = normalize_verdict(ctx.get("verdict"))
    if normalized is None:
        return GateResult(
            gate_id="verifier", passed=False, tier=VERIFIER, verdict="error",
            identity=identity, error=err, error_kind=schema_mod.error_kind(err),
            issues=[{"error": err}])
    accepted = normalized["verdict"] == "accept" and not normalized["issues"]
    return GateResult(
        gate_id="verifier", passed=accepted, tier=VERIFIER,
        verdict=normalized["verdict"], issues=normalized["issues"],
        identity=identity)


_BUILTIN_GATES: dict[str, tuple[str, GateFn]] = {
    "schema": (DETERMINISTIC, _gate_schema),
    "artifact": (DETERMINISTIC, _gate_artifact),
    "verifier": (VERIFIER, _gate_verifier),
}


class GateRunner:
    """Runs a spec's required gates in tier order. Deterministic gates first;
    verifier-tier gates are *skipped* (not run, not charged) when a required
    deterministic gate already failed — Lever 3's cost shape."""

    def __init__(self, extra_gates: Optional[dict[str, tuple[str, GateFn]]] = None):
        self._gates = dict(_BUILTIN_GATES)
        if extra_gates:
            self._gates.update(extra_gates)

    def register(self, gate_id: str, fn: GateFn, *, tier: str = DETERMINISTIC) -> None:
        self._gates[gate_id] = (tier, fn)

    def run(self, gate_ids, ctx: dict) -> dict[str, dict]:
        unknown = [g for g in gate_ids if g not in self._gates]
        if unknown:
            raise KeyError(f"unknown gate(s): {unknown}; "
                           f"registered: {sorted(self._gates)}")
        ordered = sorted(gate_ids,
                         key=lambda g: 0 if self._gates[g][0] == DETERMINISTIC else 1)
        results: dict[str, dict] = {}
        deterministic_failed = False
        for gid in ordered:
            tier, fn = self._gates[gid]
            if tier != DETERMINISTIC and deterministic_failed:
                results[gid] = GateResult(
                    gate_id=gid, passed=False, tier=tier, verdict="skipped",
                    issues=[{"error": "skipped: a deterministic gate failed"}],
                ).as_dict()
                continue
            try:
                res = fn(ctx)
            except Exception as exc:
                res = GateResult(gate_id=gid, passed=False, tier=tier,
                                 verdict="error", error=str(exc),
                                 error_kind="runtime_error",
                                 issues=[{"error": str(exc)}])
            results[gid] = res.as_dict()
            if tier == DETERMINISTIC and not res.passed:
                deterministic_failed = True
        return results


@dataclass(frozen=True)
class GoalContract:
    """Whole-run acceptance predicate over one iteration's record dict (§5.1).
    P0: boolean over gate results — non-vacuous by construction because the
    default predicate requires at least one gate result to exist."""
    predicate: Callable[[dict], bool]
    description: str = "custom predicate"

    def satisfied(self, record: dict) -> bool:
        try:
            return bool(self.predicate(record))
        except Exception:
            return False


def default_goal_contract(required_gates) -> GoalContract:
    gate_ids = list(required_gates)

    def predicate(record: dict) -> bool:
        gates = record.get("gate_results") or {}
        if not gate_ids:
            return False  # vacuous goal never auto-passes (§5.1)
        return (record.get("status") == "completed"
                and all(gates.get(g, {}).get("passed") for g in gate_ids))

    return GoalContract(predicate=predicate,
                        description=f"all required gates pass: {gate_ids}")
