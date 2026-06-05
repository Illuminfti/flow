"""Local backend — a deterministic Python callable, no model. Real work."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .base import BackendResponse


@dataclass
class LocalBackend:
    fn: Callable[..., Any]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse:
        out = self.fn(*self.args, **self.kwargs)
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
        return BackendResponse(text=text, input_tokens=0, output_tokens=0, usd=0.0,
                               provider="local", model="local", native=out)
