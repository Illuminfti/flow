"""Stable identifiers and time helpers for the Hermes Workflow engine.

``stable_hash`` is the one utility salvaged from the superseded
``albedo-dynamic-workflow.py`` — it is the content-addressed key behind both
the resume cache and leaf deduplication. Everything else here is new.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> float:
    return time.time()


def stable_hash(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def new_run_id(slug: str = "run") -> str:
    """Time-ordered, collision-resistant run id. No Math.random equivalent —
    epoch microseconds + pid give uniqueness without an RNG dependency."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    micros = int((time.time() % 1) * 1_000_000)
    return f"{slug}-{stamp}-{micros:06d}-{os.getpid():x}"


def leaf_id(*, run_script: str, phase: str, label: str, prompt: str,
            model: str, backend: str) -> str:
    """Content-addressed leaf id == resume + cache key.

    Identical (script, phase, label, prompt, model, backend) → identical id →
    a completed result is reused verbatim on resume. This is what makes a
    crashed run resume from the longest unchanged prefix.
    """
    return "l-" + stable_hash(
        {
            "run_script": run_script,
            "phase": phase,
            "label": label,
            "prompt": prompt,
            "model": model,
            "backend": backend,
        },
        length=16,
    )
