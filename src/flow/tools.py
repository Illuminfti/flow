"""First-class tools — typed functions a leaf's model can call, with per-leaf
grants and an optional approval gate.

A leaf stays a single bounded model call by default. Grant it tools and it
becomes a bounded tool-use loop (the backend drives the model→tool→model turns,
capped). Side-effecting tools can require operator approval before they run.

    from flow import register_tool, ToolDefinition

    register_tool(ToolDefinition(
        name="get_price", description="Spot price for a symbol",
        parameters={"type": "object", "required": ["symbol"],
                    "properties": {"symbol": {"type": "string"}}},
        handler=lambda symbol: {"price": prices[symbol]},
        approval=None,                      # or a callable (name, **kwargs) -> bool
    ))
    wf.agent("price of BTC?", tools=["get_price"])
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .backends.base import BackendError


class ToolBlocked(BackendError):
    """An approval gate denied a tool call. Subclasses BackendError so the leaf
    runner already turns it into a failed leaf, not a crash."""


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict                       # JSON Schema for the args
    handler: Callable[..., Any]            # called with **kwargs from the model
    approval: Optional[Callable[..., bool]] = None  # (name, **kwargs) -> bool; None = allow


_REGISTRY: dict[str, ToolDefinition] = {}


def register_tool(d: ToolDefinition) -> None:
    _REGISTRY[d.name] = d


def get_tool(name: str) -> Optional[ToolDefinition]:
    return _REGISTRY.get(name)


def to_openai_schema(names: list[str]) -> list[dict]:
    out = []
    for n in names:
        d = _REGISTRY.get(n)
        if d is None:
            raise BackendError(f"unknown tool {n!r}; register it with register_tool()")
        out.append({"type": "function",
                    "function": {"name": d.name, "description": d.description, "parameters": d.parameters}})
    return out


def to_anthropic_schema(names: list[str]) -> list[dict]:
    """Anthropic Messages API tool shape: ``{name, description, input_schema}``."""
    out = []
    for n in names:
        d = _REGISTRY.get(n)
        if d is None:
            raise BackendError(f"unknown tool {n!r}; register it with register_tool()")
        out.append({"name": d.name, "description": d.description, "input_schema": d.parameters})
    return out
