"""Leaf execution — request, result, and the backend-agnostic runner.

A leaf is one unit of model work. ``run_leaf`` resolves the route to a Backend
(see ``backends/``), calls it, and applies schema enforcement with one bounded
repair turn. Provider specifics live entirely in the backend.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import schema as schema_mod
from .backends import BackendError, BackendResponse, build_backend
from .router import Route

RawCall = BackendResponse  # back-compat alias


@dataclass
class LeafRequest:
    leaf_id: str
    label: str
    phase: str
    prompt: str
    route: Route
    toolsets: str = ""
    role: Optional[str] = None
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

    def model_call(text_prompt: str) -> BackendResponse:
        return backend(text_prompt, timeout=req.timeout)

    try:
        prompt = req.prompt
        if want_schema and not is_local:
            prompt = req.prompt + schema_mod.instruction(req.schema)
        raw = model_call(prompt)
    except BackendError as exc:
        return _fail(req, str(exc), started)
    except Exception as exc:
        return _fail(req, f"{type(exc).__name__}: {exc}", started)

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
        repaired=repaired, tokens_estimated=raw.tokens_estimated, error=error,
    )


def _fail(req: LeafRequest, error: str, started: float) -> LeafResult:
    return LeafResult(
        leaf_id=req.leaf_id, label=req.label, phase=req.phase, status="failed",
        value=None, text="", backend=req.route.backend, provider=req.route.provider,
        model=req.route.model, input_tokens=0, output_tokens=0, usd=0.0,
        elapsed_s=round(time.time() - started, 3), pid=os.getpid(), error=error[:800],
    )
