"""Budget accounting — tokens + USD + calls, with deadline propagation.

The superseded stub counted only ``agent()`` invocations. A call-count cap is
blind to a workflow that spends its whole token allowance in three giant
prompts. This enforces all three axes and is the hard ceiling the scheduler
gates every leaf against.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


class BudgetExceeded(RuntimeError):
    """Raised when dispatching a leaf would breach the run budget."""


class DeadlineExceeded(RuntimeError):
    """Raised when the run deadline has passed."""


@dataclass
class Spend:
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens": self.tokens,
            "usd": round(self.usd, 6),
            "calls": self.calls,
        }


@dataclass
class Budget:
    """A hard ceiling. ``None`` on any axis means unbounded on that axis."""

    max_tokens: Optional[int] = None
    max_usd: Optional[float] = None
    max_calls: Optional[int] = 1000  # runaway backstop, mirrors Claude Code's 1000-agent cap
    deadline_ts: Optional[float] = None
    _spend: Spend = field(default_factory=Spend)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_spec(cls, spec: Optional[dict]) -> "Budget":
        spec = spec or {}
        deadline = None
        if spec.get("deadline_seconds"):
            deadline = time.time() + float(spec["deadline_seconds"])
        return cls(
            max_tokens=spec.get("max_tokens"),
            max_usd=spec.get("max_usd"),
            max_calls=spec.get("max_calls", 1000),
            deadline_ts=deadline,
        )

    def check_deadline(self) -> None:
        if self.deadline_ts is not None and time.time() > self.deadline_ts:
            raise DeadlineExceeded(f"run deadline passed ({self.deadline_ts})")

    def leaf_deadline(self, leaf_timeout: Optional[float]) -> Optional[float]:
        """Per-leaf timeout = min(leaf's own, run remaining)."""
        run_remaining = None
        if self.deadline_ts is not None:
            run_remaining = max(0.0, self.deadline_ts - time.time())
        candidates = [t for t in (leaf_timeout, run_remaining) if t is not None]
        return min(candidates) if candidates else None

    def reserve(self, *, est_tokens: int, est_usd: float) -> None:
        """Gate BEFORE dispatch. Raises if this leaf would breach the ceiling."""
        self.check_deadline()
        with self._lock:
            if self.max_calls is not None and self._spend.calls + 1 > self.max_calls:
                raise BudgetExceeded(
                    f"call budget exhausted: {self._spend.calls}/{self.max_calls}"
                )
            if self.max_tokens is not None and self._spend.tokens + est_tokens > self.max_tokens:
                raise BudgetExceeded(
                    f"token budget would breach: {self._spend.tokens}+{est_tokens} > {self.max_tokens}"
                )
            if self.max_usd is not None and self._spend.usd + est_usd > self.max_usd:
                raise BudgetExceeded(
                    f"usd budget would breach: {self._spend.usd:.4f}+{est_usd:.4f} > {self.max_usd}"
                )
            # Optimistic call reservation; reconciled in commit().
            self._spend.calls += 1

    def commit(self, *, input_tokens: int, output_tokens: int, usd: float) -> None:
        with self._lock:
            self._spend.input_tokens += input_tokens
            self._spend.output_tokens += output_tokens
            self._spend.usd += usd

    def spend(self) -> dict:
        with self._lock:
            return self._spend.as_dict()

    def remaining(self) -> dict:
        with self._lock:
            return {
                "tokens": None if self.max_tokens is None else max(0, self.max_tokens - self._spend.tokens),
                "usd": None if self.max_usd is None else max(0.0, round(self.max_usd - self._spend.usd, 6)),
                "calls": None if self.max_calls is None else max(0, self.max_calls - self._spend.calls),
                "deadline_seconds": None if self.deadline_ts is None else max(0.0, round(self.deadline_ts - time.time(), 1)),
            }

    def has_headroom(self, *, min_tokens: int = 1) -> bool:
        r = self.remaining()
        if r["calls"] is not None and r["calls"] <= 0:
            return False
        if r["tokens"] is not None and r["tokens"] < min_tokens:
            return False
        if r["usd"] is not None and r["usd"] <= 0:
            return False
        if r["deadline_seconds"] is not None and r["deadline_seconds"] <= 0:
            return False
        return True
