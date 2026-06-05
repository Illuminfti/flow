"""Anthropic backend — optional [anthropic] extra. Falls to a clear error if the
SDK is absent (most users can route Claude via openai_http through a gateway)."""
from __future__ import annotations

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

    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse:
        try:
            import anthropic
        except ImportError:
            raise BackendError("anthropic backend needs: pip install 'flow[anthropic]'")
        try:
            client = anthropic.Anthropic(api_key=self.api_key, timeout=timeout or 60.0)
            msg = client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise BackendError(f"anthropic {self.model}: {exc}") from exc
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        real_in = getattr(msg.usage, "input_tokens", None)
        real_out = getattr(msg.usage, "output_tokens", None)
        in_tok = real_in if real_in is not None else estimate_tokens(prompt)
        out_tok = real_out if real_out is not None else estimate_tokens(text)
        return BackendResponse(
            text=text, input_tokens=int(in_tok), output_tokens=int(out_tok),
            usd=cost_usd(int(in_tok), int(out_tok), self.pricing),
            tokens_estimated=real_in is None or real_out is None,
            provider=self.provider, model=self.model,
        )
