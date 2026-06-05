"""Live progress + visual tracing.

``make_relay`` returns an on_event hook that calls an optional ``notify_fn``
(you supply it — Slack, Telegram, a print, anything). Default is silent.
``trace`` renders a run's journal as a box-drawing dashboard: phases, per-leaf
status glyphs, model badges, latency bars, cost, retries, and a summary panel.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from .paths import run_dir_for

_GLYPH = {
    "completed": "✓", "failed": "✗", "schema_failed": "⚠", "cached": "◍", "running": "◌",
}


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
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _aggregate(run_id: str) -> Optional[dict]:
    recs = _load_journal(run_id)
    if not recs:
        return None
    leaves: dict[str, dict] = {}
    phases: list[str] = []
    retries = 0
    for r in recs:
        ev = r.get("event")
        if ev == "phase":
            ph = r.get("phase")
            if ph and ph not in phases:
                phases.append(ph)
            continue
        if ev == "retry":
            retries += 1
        lid = r.get("leaf_id")
        if not lid:
            continue
        e = leaves.setdefault(lid, {"label": r.get("label"), "phase": r.get("phase"), "status": "running"})
        if ev in _GLYPH and ev != "running":
            tok = r.get("tokens") or {}
            e.update({
                "status": ev, "model": r.get("model"), "provider": r.get("provider"),
                "tokens": (tok.get("in", 0) + tok.get("out", 0)), "usd": r.get("usd", 0.0) or 0.0,
                "elapsed_s": r.get("elapsed_s", 0.0) or 0.0, "estimated": r.get("tokens_estimated", False),
                "repaired": r.get("repaired", False), "attempts": r.get("attempts", 1),
            })
    by_phase: dict[str, list[dict]] = {}
    for e in leaves.values():
        by_phase.setdefault(e.get("phase") or "?", []).append(e)
    return {
        "run_id": run_id, "phases": phases or list(by_phase), "by_phase": by_phase, "leaves": leaves,
        "total_usd": sum(e.get("usd", 0.0) for e in leaves.values()),
        "total_tok": sum(e.get("tokens", 0) for e in leaves.values()),
        "max_elapsed": max((e.get("elapsed_s", 0.0) for e in leaves.values()), default=0.0),
        "failed": sum(1 for e in leaves.values() if e.get("status") in ("failed", "schema_failed")),
        "count": len(leaves), "retries": retries,
    }


def _bar(value: float, peak: float, width: int = 10) -> str:
    if peak <= 0:
        return "░" * width
    filled = max(0, min(width, round((value / peak) * width)))
    return "█" * filled + "░" * (width - filled)


def trace(run_id: str) -> str:
    """A box-drawing run dashboard."""
    agg = _aggregate(run_id)
    if not agg:
        return f"no journal for run {run_id}"
    W = 64
    status = "failed" if agg["failed"] else "completed"
    head = "●" if status == "completed" else "✗"
    title = f" 🍃 flow · {run_id} "
    out = [f"╭{title}{'─' * max(0, W - len(title))}╮"]
    summ = (f" {head} {status}   {agg['count']} leaves · {agg['failed']} failed · "
            f"{agg['total_tok']:,} tok · ${agg['total_usd']:.4f}")
    out.append(f"│{summ:<{W}}│")
    out.append(f"╰{'─' * W}╯")
    peak = agg["max_elapsed"]
    for ph in agg["phases"]:
        items = agg["by_phase"].get(ph)
        if not items:
            continue
        hdr = f"  ▍ {ph} "
        out.append(f"{hdr}{'─' * max(0, W - len(hdr) + 1)} {len(items)} ")
        for e in items:
            g = _GLYPH.get(e.get("status", "running"), "◌")
            badge = f"{(e.get('provider') or '').split('-')[0]}·{e.get('model') or '?'}"[:22]
            flags = []
            if e.get("repaired"):
                flags.append("⟳repair")
            if (e.get("attempts") or 1) > 1:
                flags.append(f"×{e['attempts']}")
            if e.get("estimated"):
                flags.append("~tok")
            flag = ("  " + " ".join(flags)) if flags else ""
            out.append(f"  {g} {(e.get('label') or '')[:20]:<20} {badge:<22} {_bar(e.get('elapsed_s', 0.0), peak)} "
                       f"{e.get('elapsed_s', 0.0):>5.1f}s {e.get('tokens', 0):>5}t ${e.get('usd', 0.0):.4f}{flag}")
    foot = (f"  ━ {agg['count']} leaves · {agg['total_tok']:,} tok · ${agg['total_usd']:.4f} · "
            f"{agg['max_elapsed']:.1f}s peak" + (f" · {agg['retries']} retries" if agg["retries"] else ""))
    out.append(foot + " " + "━" * max(0, W - len(foot)))
    return "\n".join(out)
