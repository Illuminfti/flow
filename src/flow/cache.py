"""Cross-run content-addressed leaf cache (opt-in).

Identical leaves across runs / machines / users recompute by default. Enable
``leaf.content_cache.enabled`` and a leaf's result is keyed by its CONTENT
(prompt + schema + model + backend) and reused across run_ids. Off by default →
zero impact on existing behaviour.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .ids import stable_hash
from .journal import Journal


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "flow" / "leaves"
    d.mkdir(parents=True, exist_ok=True)
    return d


def content_key(prompt: str, schema_hash: str, model: str, backend: str) -> str:
    return stable_hash([prompt, schema_hash, model, backend], 16)


def load(key: str, ttl_days: Optional[float]) -> Optional[dict]:
    p = cache_dir() / f"{key}.json"
    if not p.exists():
        return None
    if ttl_days:
        try:
            if time.time() - p.stat().st_mtime > ttl_days * 86400:
                p.unlink(missing_ok=True)
                return None
        except OSError:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def store(key: str, payload: dict) -> None:
    try:
        Journal._atomic_write(cache_dir() / f"{key}.json", payload)
    except Exception:
        pass  # cache writes are best-effort; never fail a run over the cache
