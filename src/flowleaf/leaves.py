"""Leaf execution — request, result, and the backend-agnostic runner.

A leaf is one unit of model work. ``run_leaf`` resolves the route to a Backend
(see ``backends/``), calls it, and applies schema enforcement with one bounded
repair turn. Provider specifics live entirely in the backend.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import schema as schema_mod
from .backends import BackendError, BackendResponse, build_backend
from .constants import RETRY_BASE_MS, RETRY_CAP_MS, RETRY_MAX_ATTEMPTS
from .router import Route


@dataclass
class LeafRequest:
    leaf_id: str
    label: str
    phase: str
    prompt: str
    route: Route
    toolsets: str = ""
    schema: Any = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None
    local_fn: Optional[Callable[..., Any]] = None
    local_args: tuple = ()
    local_kwargs: dict = field(default_factory=dict)


@dataclass
class LeafResult:
    leaf_id: str
    label: str
    phase: str
    status: str            # completed | failed | schema_failed
    value: Any
    text: str
    backend: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    elapsed_s: float
    pid: int
    repaired: bool = False
    tokens_estimated: bool = False
    attempts: int = 1
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def run_leaf(req: LeafRequest) -> LeafResult:
    """Execute one leaf end-to-end (incl. schema + one repair). Normal failures
    become a LeafResult with status failed/schema_failed — never raises."""
    backend_kind = req.route.backend
    is_local = backend_kind == "local"
    started = time.time()
    want_schema = schema_mod.is_schema(req.schema)

    try:
        backend = build_backend(req)
    except Exception as exc:
        return _fail(req, f"backend init: {exc}", started)

    rc = _retry_config()
    stats = {"attempts": 0}

    def model_call(text_prompt: str) -> BackendResponse:
        # P1: retry transient (retryable) backend errors with jittered backoff.
        last: Exception = BackendError("no attempt")
        for attempt in range(rc["max_attempts"]):
            stats["attempts"] += 1
            try:
                return backend(text_prompt, timeout=req.timeout)
            except BackendError as exc:
                last = exc
                if not getattr(exc, "retryable", False) or attempt == rc["max_attempts"] - 1:
                    raise
                _backoff(attempt, rc["base_ms"], rc["cap_ms"])
        raise last

    try:
        prompt = req.prompt
        if want_schema and not is_local:
            prompt = req.prompt + schema_mod.instruction(req.schema)
        raw = model_call(prompt)
    except BackendError as exc:
        return _fail(req, str(exc), started, attempts=stats["attempts"])
    except Exception as exc:
        return _fail(req, f"{type(exc).__name__}: {exc}", started, attempts=stats["attempts"])

    value: Any = raw.native if raw.native is not None else raw.text
    repaired = False
    status = "completed"
    error = None

    if want_schema:
        ok, parsed, perr = schema_mod.parse(raw.text, req.schema)
        if ok:
            value = parsed
        elif is_local:
            status, error, value = "schema_failed", perr, parsed
        else:
            try:
                rp = schema_mod.repair_prompt(req.prompt, req.schema, raw.text, perr or "invalid")
                raw2 = model_call(rp)
                raw = BackendResponse(
                    text=raw2.text,
                    input_tokens=raw.input_tokens + raw2.input_tokens,
                    output_tokens=raw.output_tokens + raw2.output_tokens,
                    usd=raw.usd + raw2.usd,
                    tokens_estimated=raw.tokens_estimated or raw2.tokens_estimated,
                    provider=raw.provider, model=raw.model,
                )
                ok2, parsed2, perr2 = schema_mod.parse(raw2.text, req.schema)
                repaired = True
                if ok2:
                    value = parsed2
                else:
                    status, error, value = "schema_failed", perr2, parsed2
            except Exception as exc:
                status, error = "schema_failed", str(exc)[:600]

    return LeafResult(
        leaf_id=req.leaf_id, label=req.label, phase=req.phase, status=status,
        value=value, text=raw.text, backend=backend_kind,
        provider=raw.provider or req.route.provider, model=raw.model or req.route.model,
        input_tokens=raw.input_tokens, output_tokens=raw.output_tokens, usd=raw.usd,
        elapsed_s=round(time.time() - started, 3), pid=os.getpid(),
        repaired=repaired, tokens_estimated=raw.tokens_estimated,
        attempts=stats["attempts"], error=error,
    )


def _retry_config() -> dict:
    try:
        from . import config as _cfg
        rc = (_cfg.get().get("engine") or {}).get("retry") or {}
    except Exception:
        rc = {}
    return {
        "max_attempts": int(rc.get("max_attempts", RETRY_MAX_ATTEMPTS)),
        "base_ms": int(rc.get("base_ms", RETRY_BASE_MS)),
        "cap_ms": int(rc.get("cap_ms", RETRY_CAP_MS)),
    }


def _backoff(attempt: int, base_ms: int, cap_ms: int) -> None:
    delay_ms = min(cap_ms, base_ms * (2 ** attempt))
    time.sleep((delay_ms / 1000.0) * (0.5 + random.random() * 0.5))  # full jitter


def _fail(req: LeafRequest, error: str, started: float, attempts: int = 1) -> LeafResult:
    return LeafResult(
        leaf_id=req.leaf_id, label=req.label, phase=req.phase, status="failed",
        value=None, text="", backend=req.route.backend, provider=req.route.provider,
        model=req.route.model, input_tokens=0, output_tokens=0, usd=0.0,
        elapsed_s=round(time.time() - started, 3), pid=os.getpid(),
        attempts=attempts, error=error[:800],
    )
