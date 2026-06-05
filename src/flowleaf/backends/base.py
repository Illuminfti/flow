"""Backend protocol — one unit of model work, provider-agnostic.

A backend is a callable: ``prompt -> BackendResponse``. Everything else in the
engine (scheduler, journal, budget, router, schema repair) is backend-agnostic,
so adding a new model provider is just adding a Backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class BackendResponse:
    text: str
    input_tokens: int
    output_tokens: int
    usd: float = 0.0
    tokens_estimated: bool = False
    provider: str = ""
    model: str = ""
    native: Any = None  # local backend preserves the real Python return value


class BackendError(RuntimeError):
    """Any failure producing a model response. The scheduler turns this into a
    failed leaf, never a crash."""


@runtime_checkable
class Backend(Protocol):
    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse: ...
