"""OpenAI-compatible HTTP backend — the universal default.

Every major provider (OpenAI, DeepSeek, Together, Groq, Mistral, OpenRouter,
Ollama, LM Studio, vLLM, ...) speaks ``POST /chat/completions``. Uses stdlib
``urllib`` so the core has ZERO required dependencies; if ``httpx`` is installed
it is used for speed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

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

    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            data = self._post(url, body, headers, timeout or 60.0)
        except Exception as exc:
            raise BackendError(f"openai_http {self.provider}/{self.model}: {exc}") from exc
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

    @staticmethod
    def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
        payload = json.dumps(body).encode("utf-8")
        try:  # prefer httpx if present
            import httpx
            r = httpx.post(url, content=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except ImportError:
            pass
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {e.code}: {detail}")
