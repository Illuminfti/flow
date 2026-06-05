"""Anthropic backend — optional [anthropic] extra. Falls to a clear error if the
SDK is absent (most users can route Claude via openai_http through a gateway).

Supports first-class tools natively (Messages API ``tool_use``/``tool_result``
loop), at parity with the openai_http backend: per-leaf grants, approval gates,
a bounded loop capped at ``max_tool_iterations``, and accumulated token/usd
accounting across turns.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .base import BackendError, BackendResponse
from .pricing import cost_usd, estimate_tokens


@dataclass
class AnthropicBackend:
    api_key: str
    model: str
    provider: str = "anthropic"
    max_tokens: int = 4096
    pricing: tuple[float, float] = (0.0, 0.0)
    max_tool_iterations: int = 8

    def __call__(self, prompt: str, *, timeout: float | None = None,
                 tools: list | None = None, approval_gates: dict | None = None) -> BackendResponse:
        try:
            import anthropic
        except ImportError:
            raise BackendError("anthropic backend needs: pip install 'flow[anthropic]'")
        try:
            client = anthropic.Anthropic(api_key=self.api_key, timeout=timeout or 60.0)
        except Exception as exc:
            raise self._err(exc)
        if tools:
            return self._tool_loop(client, prompt, tools, approval_gates or {})
        try:
            msg = client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise self._err(exc)
        text = _text_of(msg)
        in_tok, out_tok, estimated = _usage_of(msg, prompt, text)
        return BackendResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            usd=cost_usd(in_tok, out_tok, self.pricing),
            tokens_estimated=estimated, provider=self.provider, model=self.model,
        )

    def _tool_loop(self, client, prompt: str, tools: list, gates: dict) -> BackendResponse:
        """model → tool_use → execute (with approval) → tool_result → repeat,
        capped at max_tool_iterations. Approval denial raises ToolBlocked."""
        from ..tools import ToolBlocked, get_tool, to_anthropic_schema
        schema = to_anthropic_schema(tools)
        messages: list = [{"role": "user", "content": prompt}]
        in_tok = out_tok = calls_made = 0
        estimated = False
        for _ in range(max(1, self.max_tool_iterations)):
            try:
                msg = client.messages.create(
                    model=self.model, max_tokens=self.max_tokens,
                    messages=messages, tools=schema,
                )
            except Exception as exc:
                raise self._err(exc)
            u = getattr(msg, "usage", None)
            ri = getattr(u, "input_tokens", None)
            ro = getattr(u, "output_tokens", None)
            in_tok += int(ri) if ri is not None else 0
            out_tok += int(ro) if ro is not None else 0
            estimated = estimated or ri is None or ro is None
            blocks = list(getattr(msg, "content", []) or [])
            tool_uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
            if getattr(msg, "stop_reason", "") != "tool_use" and not tool_uses:
                text = _text_of(msg)
                if not (in_tok or out_tok):
                    in_tok, out_tok, estimated = estimate_tokens(prompt), estimate_tokens(text), True
                return BackendResponse(
                    text=text, input_tokens=in_tok, output_tokens=out_tok,
                    usd=cost_usd(in_tok, out_tok, self.pricing), tokens_estimated=estimated,
                    provider=self.provider, model=self.model, tool_calls_made=calls_made,
                )
            messages.append({"role": "assistant", "content": blocks})
            results = []
            for tu in tool_uses:
                name = getattr(tu, "name", "")
                args = getattr(tu, "input", None) or {}
                tuid = getattr(tu, "id", "")
                td = get_tool(name)
                if td is None:
                    results.append({"type": "tool_result", "tool_use_id": tuid,
                                    "content": json.dumps({"error": f"unknown tool {name}"}),
                                    "is_error": True})
                    calls_made += 1
                    continue
                gate = gates.get(name) or td.approval
                if gate is not None and not gate(name, **args):
                    raise ToolBlocked(f"tool_blocked: {name} denied by approval gate")
                out = td.handler(**args)
                results.append({"type": "tool_result", "tool_use_id": tuid,
                                "content": json.dumps(out, default=str)})
                calls_made += 1
            messages.append({"role": "user", "content": results})
        raise BackendError(f"tool loop exceeded {self.max_tool_iterations} iterations")

    def _err(self, exc: Exception) -> BackendError:
        if isinstance(exc, BackendError):
            return exc
        status = getattr(exc, "status_code", None)
        retryable = None
        if status is None and any(s in type(exc).__name__ for s in ("Connection", "Timeout")):
            retryable = True  # network/timeout → transient
        return BackendError(f"anthropic {self.model}: {exc}", status=status, retryable=retryable)


def _text_of(msg) -> str:
    return "".join(
        getattr(b, "text", "") for b in (getattr(msg, "content", None) or [])
        if getattr(b, "type", "") == "text"
    ).strip()


def _usage_of(msg, prompt: str, text: str) -> tuple[int, int, bool]:
    u = getattr(msg, "usage", None)
    real_in = getattr(u, "input_tokens", None)
    real_out = getattr(u, "output_tokens", None)
    in_tok = int(real_in) if real_in is not None else estimate_tokens(prompt)
    out_tok = int(real_out) if real_out is not None else estimate_tokens(text)
    return in_tok, out_tok, real_in is None or real_out is None
