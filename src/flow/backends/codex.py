"""Codex backend — ChatGPT Pro/Plus subscription via the Codex Responses API.

Uses your ChatGPT OAuth token (no per-token API billing) against
``https://chatgpt.com/backend-api/codex/responses``. This is the OpenAI
*Responses* API (not chat/completions): payload must be
``{model, instructions, input:[...], store:false, stream:true}`` and the
response is SSE — collect ``response.output_text.delta`` events.

Config:
    providers:
      codex: {kind: codex, auth_file: "~/.codex/auth.json",
              auth_field: "tokens.access_token", account_field: "tokens.account_id"}
    models:
      gpt55: {provider: codex, id: gpt-5.5, in: 0, out: 0, free: true,
              caps: [reasoning, tool_call, structured]}

Note: subscription OAuth is intended for ordinary use of the vendor's own
apps; routing through it is your call, and tokens/limits are the vendor's.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .base import BackendError, BackendResponse
from .pricing import estimate_tokens

_DEFAULT_BASE = "https://chatgpt.com/backend-api/codex"
_DEFAULT_INSTRUCTIONS = "You are a precise assistant. Follow the user's instructions exactly."


@dataclass
class CodexBackend:
    token: str
    model: str
    account_id: str = ""
    base_url: str = _DEFAULT_BASE
    instructions: str = _DEFAULT_INSTRUCTIONS
    provider: str = "openai-codex"

    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse:
        if not self.token:
            raise BackendError("codex: no OAuth token (check auth_file/auth_field)")
        body = json.dumps({
            "model": self.model,
            "instructions": self.instructions,
            "input": [{"role": "user", "content": prompt}],
            "store": False,
            "stream": True,
        }).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if self.account_id:
            headers["chatgpt-account-id"] = self.account_id
        url = self.base_url.rstrip("/") + "/responses"
        try:
            text = self._stream(url, body, headers, timeout or 600.0)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"codex {self.model}: {exc}", retryable=True) from exc
        in_tok = estimate_tokens(prompt)
        out_tok = estimate_tokens(text)
        return BackendResponse(text=text, input_tokens=in_tok, output_tokens=out_tok,
                               usd=0.0, tokens_estimated=True, provider=self.provider, model=self.model)

    @staticmethod
    def _collect(line: str, acc: list) -> None:
        if not line.startswith("data:"):
            return
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except Exception:
            return
        if event.get("type") == "response.output_text.delta":
            acc.append(event.get("delta", ""))

    def _stream(self, url: str, body: bytes, headers: dict, timeout: float) -> str:
        acc: list = []
        try:  # prefer httpx for robust streaming
            import httpx
            with httpx.stream("POST", url, content=body, headers=headers, timeout=timeout) as r:
                if r.status_code >= 400:
                    raise BackendError(f"codex HTTP {r.status_code}: {r.read()[:300]!r}", status=r.status_code)
                for line in r.iter_lines():
                    self._collect(line if isinstance(line, str) else line.decode("utf-8", "replace"), acc)
            return "".join(acc).strip()
        except ImportError:
            pass
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw in resp:
                    self._collect(raw.decode("utf-8", "replace"), acc)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise BackendError(f"codex HTTP {e.code}: {detail}", status=e.code)
        return "".join(acc).strip()
