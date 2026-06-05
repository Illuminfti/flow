"""OpenAI-compatible HTTP backend — the universal default.

Every major provider (OpenAI, DeepSeek, Together, Groq, Mistral, OpenRouter,
Ollama, LM Studio, vLLM, ...) speaks ``POST /chat/completions``. Uses stdlib
``urllib`` so the core has ZERO required dependencies; if ``httpx`` is installed
it is used for speed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .base import BackendError, BackendResponse
from .pricing import cost_usd, estimate_tokens


@dataclass
class OpenAIHTTPBackend:
    base_url: str
    api_key: str
    model: str
    provider: str = "openai-compatible"
    max_tokens: int | None = None
    temperature: float = 0.7
    pricing: tuple[float, float] = (0.0, 0.0)
    extra_headers: dict = field(default_factory=dict)  # OAuth / vendor headers
    max_tool_iterations: int = 8

    def __call__(self, prompt: str, *, timeout: float | None = None,
                 tools: list | None = None, approval_gates: dict | None = None) -> BackendResponse:
        if tools:
            return self._tool_loop(prompt, tools, approval_gates or {}, timeout)
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", **(self.extra_headers or {})}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            data = self._post(url, body, headers, timeout or 60.0)
        except BackendError:
            raise  # already tagged with status/retryable
        except Exception as exc:
            # network/timeout/connection errors are transient → retryable
            raise BackendError(f"openai_http {self.provider}/{self.model}: {exc}", retryable=True) from exc
        try:
            text = (data["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"openai_http malformed response: {exc}; got {str(data)[:300]}")
        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or estimate_tokens(prompt))
        out_tok = int(usage.get("completion_tokens") or estimate_tokens(text))
        return BackendResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            usd=cost_usd(in_tok, out_tok, self.pricing),
            tokens_estimated=not usage, provider=self.provider, model=self.model,
        )

    def _tool_loop(self, prompt: str, tools: list, gates: dict, timeout: float | None) -> BackendResponse:
        """model → tool_calls → execute (with approval) → feed results → repeat,
        capped at max_tool_iterations. Approval denial raises ToolBlocked."""
        from ..tools import ToolBlocked, get_tool, to_openai_schema
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", **(self.extra_headers or {})}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        messages = [{"role": "user", "content": prompt}]
        schema = to_openai_schema(tools)
        in_tok = out_tok = calls_made = 0
        for _ in range(max(1, self.max_tool_iterations)):
            body = {"model": self.model, "messages": messages, "temperature": self.temperature, "tools": schema}
            if self.max_tokens:
                body["max_tokens"] = self.max_tokens
            try:
                data = self._post(url, body, headers, timeout or 60.0)
            except BackendError:
                raise
            except Exception as exc:
                raise BackendError(f"openai_http {self.provider}/{self.model}: {exc}", retryable=True) from exc
            u = data.get("usage") or {}
            in_tok += int(u.get("prompt_tokens") or 0)
            out_tok += int(u.get("completion_tokens") or 0)
            msg = data["choices"][0]["message"]
            tcs = msg.get("tool_calls") or []
            if not tcs:
                text = (msg.get("content") or "").strip()
                return BackendResponse(text=text, input_tokens=in_tok or estimate_tokens(prompt),
                                       output_tokens=out_tok or estimate_tokens(text),
                                       usd=cost_usd(in_tok, out_tok, self.pricing),
                                       tokens_estimated=not (in_tok or out_tok),
                                       provider=self.provider, model=self.model, tool_calls_made=calls_made)
            messages.append(msg)
            for tc in tcs:
                fn = tc["function"]["name"]
                args = json.loads(tc["function"].get("arguments") or "{}")
                td = get_tool(fn)
                if td is None:
                    result = {"error": f"unknown tool {fn}"}
                else:
                    gate = gates.get(fn) or td.approval
                    if gate is not None and not gate(fn, **args):
                        raise ToolBlocked(f"tool_blocked: {fn} denied by approval gate")
                    result = td.handler(**args)
                calls_made += 1
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": json.dumps(result, default=str)})
        raise BackendError(f"tool loop exceeded {self.max_tool_iterations} iterations")

    @staticmethod
    def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
        payload = json.dumps(body).encode("utf-8")
        try:
            import httpx
        except ImportError:
            httpx = None
        if httpx is not None:  # prefer httpx; classify HTTP status (don't lose it)
            r = httpx.post(url, content=payload, headers=headers, timeout=timeout)
            if r.status_code >= 400:
                raise BackendError(f"HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)
            return r.json()
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise BackendError(f"HTTP {e.code}: {detail}", status=e.code)
