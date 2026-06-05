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
    tool_calls_made: int = 0  # tool turns executed in a tool-use loop


class BackendError(RuntimeError):
    """Any failure producing a model response. The scheduler turns this into a
    failed leaf, never a crash.

    ``retryable`` (or an HTTP ``status`` in {408,409,425,429,500,502,503,504})
    tells the leaf runner to retry with backoff. Auth/4xx errors are terminal.
    """

    def __init__(self, message: str, *, status: int | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.status = status
        if retryable is None:
            retryable = status in {408, 409, 425, 429, 500, 502, 503, 504} if status else False
        self.retryable = retryable


@runtime_checkable
class Backend(Protocol):
    def __call__(self, prompt: str, *, timeout: float | None = None,
                 tools: "list | None" = None, approval_gates: "dict | None" = None) -> BackendResponse: ...
