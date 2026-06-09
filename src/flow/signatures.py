"""Failure signatures, stall detection, repair routing (plan §6 L6, WS-E).

A :class:`FailureSignature` fingerprints a recurring failure as
``(error_kind, normalized_message, gate_id)`` so identical rejections across
iterations dedupe to one identity. The registry is rebuilt deterministically
from iteration records (never from in-memory state), which is what makes
``stop_reason`` replay-stable (invariant I3, §7.1).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

from .ids import stable_hash

_HEXISH = re.compile(r"\b[0-9a-f]{6,}\b")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def normalize_message(msg: Optional[str], limit: int = 160) -> str:
    """Strip the volatile parts (numbers, hashes, whitespace runs) so the same
    failure produced twice fingerprints identically."""
    text = (msg or "").lower()
    text = _HEXISH.sub("#", text)
    text = _NUM.sub("#", text)
    text = _WS.sub(" ", text).strip()
    return text[:limit]


@dataclass(frozen=True)
class FailureSignature:
    error_kind: str
    message: str       # normalized
    gate_id: str = ""

    def fingerprint(self) -> str:
        return stable_hash((self.error_kind, self.message, self.gate_id), length=12)

    def as_dict(self) -> dict:
        return {"error_kind": self.error_kind, "message": self.message,
                "gate_id": self.gate_id, "fingerprint": self.fingerprint()}


def signatures_from_record(record: dict) -> list[FailureSignature]:
    """Extract every failure signature one iteration record carries: leaf/step
    failures plus failed gate results."""
    sigs: list[FailureSignature] = []
    for f in record.get("failures") or ():
        sigs.append(FailureSignature(
            error_kind=str(f.get("error_kind") or "runtime_error"),
            message=normalize_message(f.get("error"))))
    for gid, g in (record.get("gate_results") or {}).items():
        if g.get("passed"):
            continue
        issue = (g.get("issues") or [""])[0]
        if isinstance(issue, dict):
            issue = issue.get("error") or issue
        sigs.append(FailureSignature(
            error_kind=str(g.get("error_kind") or f"gate_{g.get('verdict') or 'reject'}"),
            message=normalize_message(g.get("error") or str(issue)),
            gate_id=str(gid)))
    return sigs


class FailureSignatureRegistry:
    """In-run (and, via journal replay, cross-run) registry of recurring
    failures. Deterministic: built purely from iteration records."""

    def __init__(self) -> None:
        self._counts: Counter[FailureSignature] = Counter()
        self._by_iteration: dict[int, frozenset[str]] = {}

    @classmethod
    def from_records(cls, records: Iterable[dict]) -> "FailureSignatureRegistry":
        reg = cls()
        for rec in records:
            reg.record(rec)
        return reg

    def record(self, record: dict) -> list[FailureSignature]:
        sigs = signatures_from_record(record)
        for s in sigs:
            self._counts[s] += 1
        self._by_iteration[int(record.get("iteration") or 0)] = frozenset(
            s.fingerprint() for s in sigs)
        return sigs

    def count(self, sig: FailureSignature) -> int:
        return self._counts[sig]

    def top(self, n: int = 5) -> list[dict]:
        return [{**s.as_dict(), "count": c} for s, c in self._counts.most_common(n)]

    def iteration_fingerprints(self, n: int) -> frozenset[str]:
        return self._by_iteration.get(n, frozenset())


def stall_key(record: dict) -> str:
    """One iteration's evidence fingerprint: candidate content + failure
    signatures + gate pass/fail shape. Two iterations with the same stall key
    produced no new evidence (Lever 6)."""
    return stable_hash({
        "candidate": stable_hash(record.get("candidate")),
        "sigs": sorted(s.fingerprint() for s in signatures_from_record(record)),
        "gates": {gid: bool(g.get("passed"))
                  for gid, g in (record.get("gate_results") or {}).items()},
    })


def is_stalled(records: list[dict], *, stall_limit: int) -> bool:
    """True when the last ``stall_limit`` iterations are evidence-identical AND
    unaccepted — repeated equivalent outputs, unchanged artifacts, or the same
    rejection over and over. Stops the loop before the token wall."""
    if stall_limit < 2 or len(records) < stall_limit:
        return False
    window = records[-stall_limit:]
    keys = {stall_key(r) for r in window}
    if len(keys) != 1:
        return False
    # An all-green window isn't a stall; goal_met (higher precedence) or
    # max_iterations owns that outcome.
    def _unaccepted(rec: dict) -> bool:
        if rec.get("status") != "completed":
            return True
        gates = rec.get("gate_results") or {}
        return any(not g.get("passed") for g in gates.values())
    return all(_unaccepted(r) for r in window)


class RepairRouter:
    """Selects a repair strategy by failure signature (WS-E). v1 had two fixed
    one-shot repairs (schema, authoring); the router makes selection explicit
    and extensible without changing the call sites."""

    _BY_KIND = {
        "parse_error": "verifier_repair",
        "schema_error": "verifier_repair",
        "runtime_error": "retry",
    }

    def __init__(self, overrides: Optional[dict[str, str]] = None):
        self._by_kind = {**self._BY_KIND, **(overrides or {})}

    def route(self, sig: FailureSignature) -> str:
        if sig.message.startswith("budget:"):
            return "none"  # never repair a budget breach by retrying
        if sig.gate_id == "verifier" and sig.error_kind in ("parse_error", "schema_error"):
            return "verifier_repair"
        return self._by_kind.get(sig.error_kind, "none")
