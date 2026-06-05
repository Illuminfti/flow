"""Token estimation + cost helpers."""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """~4 chars/token. Used when a backend cannot report real usage."""
    if not text:
        return 1
    return max(1, len(text) // 4)


def cost_usd(in_tokens: int, out_tokens: int, pricing: tuple[float, float]) -> float:
    pin, pout = pricing
    return (in_tokens / 1e6) * pin + (out_tokens / 1e6) * pout
