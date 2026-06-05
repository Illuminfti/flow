"""Live progress + distributed tracing.

``make_relay`` returns an on_event hook that calls an optional ``notify_fn``
(you supply it — Slack, Telegram, a print, anything). Default is silent.
``trace`` renders a run's journal as a per-leaf span tree.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from .paths import run_dir_for


def make_relay(*, run_id: str, notify_fn: Optional[Callable[[str, str], None]] = None,
               debounce_s: float = 15.0) -> Callable[[dict], None]:
    """on_event hook. Escalates phase + notify events; debounces completions.
    No-op if notify_fn is None."""
    state = {"last": 0.0, "completed": 0}

    def emit(title: str, body: str) -> None:
        if notify_fn is None:
            return
        try:
            notify_fn(title, body)
        except Exception:
            pass

    def hook(rec: dict) -> None:
        ev = rec.get("event")
        if ev == "completed":
            state["completed"] += 1
        now = time.time()
        if ev == "phase":
            emit(f"flow {run_id}", f"phase: {rec.get('phase')}")
        elif rec.get("notify"):
            emit(f"flow {run_id}", rec.get("message", ""))
        elif ev == "completed" and now - state["last"] > debounce_s:
            state["last"] = now
            emit(f"flow {run_id}", f"{state['completed']} leaves done")

    return hook


def _load_journal(run_id: str) -> list[dict]:
    jp = run_dir_for(run_id) / "journal.jsonl"
    if not jp.exists():
        return []
    out = []
    for line in jp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def trace(run_id: str) -> str:
    recs = _load_journal(run_id)
    if not recs:
        return f"no journal for run {run_id}"
    leaves: dict[str, dict] = {}
    phases: list[str] = []
    for r in recs:
        ev = r.get("event")
        if ev == "phase":
            ph = r.get("phase")
            if ph and ph not in phases:
                phases.append(ph)
            continue
        lid = r.get("leaf_id")
        if not lid:
            continue
        e = leaves.setdefault(lid, {"label": r.get("label"), "phase": r.get("phase")})
        if ev in ("completed", "failed", "schema_failed", "cached"):
            tok = r.get("tokens") or {}
            e.update({"status": ev, "model": r.get("model"), "provider": r.get("provider"),
                      "tokens": (tok.get("in", 0) + tok.get("out", 0)), "usd": r.get("usd", 0.0),
                      "elapsed_s": r.get("elapsed_s", 0.0), "estimated": r.get("tokens_estimated", False),
                      "repaired": r.get("repaired", False)})

    by_phase: dict[str, list[dict]] = {}
    for e in leaves.values():
        by_phase.setdefault(e.get("phase") or "?", []).append(e)

    lines = [f"trace {run_id}"]
    total_usd = total_tok = 0
    icon = {"completed": "ok", "failed": "FAIL", "schema_failed": "SCHEMA", "cached": "cache"}
    for ph in (phases or list(by_phase)):
        if ph not in by_phase:
            continue
        lines.append(f"  phase: {ph}")
        for e in by_phase[ph]:
            st = e.get("status", "running")
            total_usd += e.get("usd", 0.0) or 0.0
            total_tok += e.get("tokens", 0) or 0
            flags = []
            if e.get("estimated"):
                flags.append("~tok")
            if e.get("repaired"):
                flags.append("repaired")
            flag = (" [" + ",".join(flags) + "]") if flags else ""
            lines.append(f"    [{icon.get(st, st)}] {e.get('label')} "
                         f"{e.get('provider') or ''}/{e.get('model') or ''} "
                         f"{e.get('tokens', 0)}tok ${e.get('usd', 0.0):.4f} {e.get('elapsed_s', 0.0)}s{flag}")
    lines.append(f"  total: {len(leaves)} leaves, {total_tok} tok, ${total_usd:.4f}")
    return "\n".join(lines)
