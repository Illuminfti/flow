"""Per-leaf cost-aware, capability-aware model router — config-driven.

Reads models / tiers / providers / denylist from the active config (see
``config.py``). No vendor catalog is baked in. Selection: capability filter →
tier order → minimum estimated cost. Never silently downgrades — an impossible
request raises ``RouterError``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from . import config as _config


class RouterError(RuntimeError):
    pass


@dataclass
class Route:
    provider: str
    model: str
    backend: str           # provider kind: openai_http | anthropic_sdk | shell_cmd | local
    tier: str
    pricing: tuple         # (in_per_mtok, out_per_mtok)
    free: bool
    why: str

    def est_usd(self, est_in: int, est_out: int) -> float:
        pin, pout = self.pricing
        return (est_in / 1e6) * pin + (est_out / 1e6) * pout

    def as_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "backend": self.backend,
                "tier": self.tier, "free": self.free, "why": self.why}


def _denylist(cfg: dict):
    leaf = cfg.get("leaf") or {}
    if leaf.get("allow_light_models"):
        return None
    pat = leaf.get("denylist")
    return re.compile(pat, re.IGNORECASE) if pat else None


def _provider_kind(cfg: dict, provider_name: str) -> str:
    prov = (cfg.get("providers") or {}).get(provider_name)
    if not prov:
        raise RouterError(f"unknown provider {provider_name!r}; define it under providers:")
    return prov.get("kind", "openai_http")


def _pick_backend(cfg, *, model_entry, tier, forced) -> str:
    if forced:
        return forced
    if tier == "local":
        return "local"
    return _provider_kind(cfg, model_entry["provider"])


def choose(*, tier: Optional[str] = None, model: Optional[str] = None,
           provider: Optional[str] = None, toolsets: Optional[str] = None,
           role: Optional[str] = None, backend: Optional[str] = None,
           needs: Optional[set] = None,
           est_in_tokens: int = 800, est_out_tokens: int = 400) -> Route:
    cfg = _config.get()
    models = cfg.get("models") or {}
    tiers = cfg.get("tiers") or {}
    deny = _denylist(cfg)
    needs = set(needs or [])

    # Explicit model pin (by config key or raw id).
    if model:
        entry = models.get(model)
        model_id = entry["id"] if entry else model
        if deny and deny.search(model_id):
            raise RouterError(f"model {model_id!r} matches the light-model denylist "
                              f"(set leaf.allow_light_models: true to permit)")
        prov_name = provider or (entry["provider"] if entry else None)
        if not prov_name:
            raise RouterError(f"model {model!r} not in config; pass provider= or add it under models:")
        pricing = (entry.get("in", 0.0), entry.get("out", 0.0)) if entry else (0.0, 0.0)
        free = bool(entry and entry.get("free"))
        bk = backend or _provider_kind(cfg, prov_name)
        return Route(prov_name, model_id, bk, tier or "pinned", pricing, free, f"explicit pin {model}")

    tier = tier or (cfg.get("defaults") or {}).get("tier", "quality")
    keys = tiers.get(tier)
    if keys is None:
        raise RouterError(f"unknown tier {tier!r}; define it under tiers: ({sorted(tiers)})")
    if tier == "local":
        # local tier carries no model; caller should use wf.local
        return Route("local", "local", "local", "local", (0.0, 0.0), True, "tier=local")

    scored = []
    for key in keys:
        entry = models.get(key)
        if not entry:
            continue
        if deny and deny.search(entry["id"]):
            continue
        if needs and not needs.issubset(set(entry.get("caps") or [])):
            continue
        cost = (est_in_tokens / 1e6) * entry.get("in", 0.0) + (est_out_tokens / 1e6) * entry.get("out", 0.0)
        scored.append((cost, key, entry))

    if not scored:
        raise RouterError(f"no model in tier {tier!r} satisfies caps {sorted(needs)} "
                          f"(check your config models/tiers)")
    scored.sort(key=lambda x: (x[0], 0 if x[2].get("free") else 1))
    cost, key, entry = scored[0]
    bk = _pick_backend(cfg, model_entry=entry, tier=tier, forced=backend)
    return Route(entry["provider"], entry["id"], bk, tier,
                 (entry.get("in", 0.0), entry.get("out", 0.0)), bool(entry.get("free")),
                 f"tier={tier} min-cost {[k for _, k, _ in scored]} -> {key} (~${cost:.4f})")
