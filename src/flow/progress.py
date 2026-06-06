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


def resume_plan(run_id: str) -> dict:
    """What `flow resume` would do per leaf: 'skip' (terminal + result file on
    disk) or 'rerun' (interrupted / cancelled / failed). Includes the estimated
    USD of the rerun set."""
    from .journal import Journal
    agg = _aggregate(run_id)
    if not agg:
        return {"plan": {}, "skip": 0, "rerun": 0, "estimated_rerun_usd": 0.0}
    skip_ids = set(Journal(run_dir_for(run_id)).completed_leaves().keys())
    plan, rerun_usd = {}, 0.0
    for lid, e in agg["leaves"].items():
        if lid in skip_ids:
            plan[lid] = "skip"
        else:
            plan[lid] = "rerun"
            rerun_usd += e.get("usd", 0.0) or 0.0
    return {"plan": plan, "skip": sum(1 for v in plan.values() if v == "skip"),
            "rerun": sum(1 for v in plan.values() if v == "rerun"),
            "estimated_rerun_usd": round(rerun_usd, 6)}


def status_report(run_id: str) -> Optional[dict]:
    """Inspectable durable workflow state: node-state manifest + resume plan."""
    agg = _aggregate(run_id)
    if not agg:
        return None
    nodes = {lid: {"label": e.get("label"), "phase": e.get("phase"), "status": e.get("status"),
                   "attempts": e.get("attempts", 1), "model": e.get("model"),
                   "provider": e.get("provider"), "tokens": e.get("tokens", 0), "usd": e.get("usd", 0.0)}
             for lid, e in agg["leaves"].items()}
    return {"run_id": run_id, "phases": agg["phases"], "nodes": nodes,
            "count": agg["count"], "failed": agg["failed"], "retries": agg["retries"],
            "total_usd": agg["total_usd"], "total_tok": agg["total_tok"],
            "resume": resume_plan(run_id)}


def render_status(report: Optional[dict]) -> str:
    if not report:
        return "no state for this run"
    g = {"completed": "✓", "failed": "✗", "schema_failed": "⚠", "cached": "◍",
         "cancelled": "⊘", "running": "◌"}
    lines = [f"flow status · {report['run_id']}",
             f"  {report['count']} nodes · {report['failed']} failed · "
             f"{report['total_tok']:,} tok · ${report['total_usd']:.4f}"]
    by_phase: dict = {}
    for lid, n in report["nodes"].items():
        by_phase.setdefault(n.get("phase") or "?", []).append(n)
    for ph in report["phases"]:
        if ph not in by_phase:
            continue
        lines.append(f"  ▍ {ph}")
        for n in by_phase[ph]:
            att = f" ×{n['attempts']}" if (n.get("attempts") or 1) > 1 else ""
            lines.append(f"    {g.get(n['status'], '?')} {n['label']}{att}")
    r = report["resume"]
    lines.append(f"  resume impact: {r['rerun']} rerun · {r['skip']} skip · ~${r['estimated_rerun_usd']:.4f}")
    return "\n".join(lines)


def spans(run_id: str) -> list:
    """Structured per-leaf spans (the machine-readable form of ``trace``)."""
    agg = _aggregate(run_id)
    if not agg:
        return []
    out = []
    for ph in agg["phases"]:
        for e in agg["by_phase"].get(ph, []):
            out.append({"phase": ph, "label": e.get("label"), "status": e.get("status"),
                        "provider": e.get("provider"), "model": e.get("model"),
                        "tokens": e.get("tokens", 0), "usd": e.get("usd", 0.0),
                        "elapsed_s": e.get("elapsed_s", 0.0), "attempts": e.get("attempts", 1)})
    return out


def _bar(value: float, peak: float, width: int = 10) -> str:
    if peak <= 0:
        return "░" * width
    filled = max(0, min(width, round((value / peak) * width)))
    return "█" * filled + "░" * (width - filled)


_CARD_COLOR = {"completed": "#3fb950", "failed": "#f85149", "schema_failed": "#d29922",
               "cached": "#58a6ff", "running": "#8b949e"}

_CARD_CSS = """
*{margin:0;padding:0;box-sizing:border-box;font-family:'SF Mono','JetBrains Mono',ui-monospace,monospace}
body{background:#0d1117;color:#e6edf3;padding:28px;width:720px}
.hd{display:flex;align-items:center;gap:12px;margin-bottom:6px}
.leaf{font-size:30px}
.title{font-size:22px;font-weight:700;letter-spacing:.5px}
.run{color:#8b949e;font-size:13px;margin-left:auto}
.pill{display:inline-block;padding:3px 12px;border-radius:999px;font-size:13px;font-weight:700}
.pill.ok{background:rgba(63,185,80,.15);color:#3fb950;border:1px solid #238636}
.pill.fail{background:rgba(248,81,73,.15);color:#f85149;border:1px solid #da3633}
.sub{color:#8b949e;font-size:14px;margin:10px 0 18px}
.sub b{color:#e6edf3}
.phase{margin:16px 0 8px;color:#58a6ff;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1px;
  border-bottom:1px solid #21262d;padding-bottom:6px;display:flex;justify-content:space-between}
.leafrow{display:flex;align-items:center;gap:10px;padding:6px 0;font-size:14px}
.g{width:18px;text-align:center;font-weight:700}
.lbl{flex:0 0 180px;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.badge{flex:0 0 150px;font-size:11px;color:#c9d1d9;background:#161b22;border:1px solid #30363d;
  border-radius:6px;padding:2px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.track{flex:1;height:8px;background:#161b22;border-radius:4px;overflow:hidden}
.fill{height:100%;border-radius:4px}
.meta{flex:0 0 150px;text-align:right;color:#8b949e;font-size:12px}
.foot{margin-top:22px;padding-top:14px;border-top:1px solid #21262d;display:flex;justify-content:space-between;
  color:#8b949e;font-size:13px}
.foot b{color:#e6edf3}
.tag{color:#d29922;font-size:11px;margin-left:6px}
"""


def card_html(run_id: str) -> Optional[str]:
    """A self-contained dark HTML dashboard for a run (the visual form of ``trace``)."""
    agg = _aggregate(run_id)
    if not agg:
        return None
    ok = not agg["failed"]
    peak = agg["max_elapsed"] or 1.0
    rows = []
    for ph in agg["phases"]:
        items = agg["by_phase"].get(ph)
        if not items:
            continue
        rows.append(f'<div class="phase"><span>{ph}</span><span>{len(items)} leaves</span></div>')
        for e in items:
            st = e.get("status", "running")
            g = _GLYPH.get(st, "◌")
            color = _CARD_COLOR.get(st, "#8b949e")
            pct = max(4, min(100, round((e.get("elapsed_s", 0.0) / peak) * 100)))
            badge = f"{(e.get('provider') or '').split('-')[0]}·{e.get('model') or '?'}"
            tags = ""
            if e.get("repaired"):
                tags += '<span class="tag">⟳repair</span>'
            if (e.get("attempts") or 1) > 1:
                tags += f'<span class="tag">×{e["attempts"]}</span>'
            if e.get("estimated"):
                tags += '<span class="tag">~tok</span>'
            rows.append(
                f'<div class="leafrow"><span class="g" style="color:{color}">{g}</span>'
                f'<span class="lbl">{(e.get("label") or "")}{tags}</span>'
                f'<span class="badge">{badge}</span>'
                f'<span class="track"><span class="fill" style="width:{pct}%;background:{color}"></span></span>'
                f'<span class="meta">{e.get("elapsed_s", 0.0):.1f}s · {e.get("tokens", 0)}t · '
                f'${e.get("usd", 0.0):.4f}</span></div>'
            )
    pill, pill_txt = ("ok", "completed") if ok else ("fail", "failed")
    retries = f' · {agg["retries"]} retries' if agg["retries"] else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CARD_CSS}</style></head><body>
<div class="hd"><span class="leaf">🍃</span><span class="title">flow</span>
<span class="run">{run_id}</span></div>
<div><span class="pill {pill}">{pill_txt}</span></div>
<div class="sub"><b>{agg['count']}</b> leaves · <b>{agg['failed']}</b> failed ·
<b>{agg['total_tok']:,}</b> tokens · <b>${agg['total_usd']:.4f}</b> · <b>{agg['max_elapsed']:.1f}s</b> peak{retries}</div>
{''.join(rows)}
<div class="foot"><span>{len(agg['phases'])} phases</span>
<span><b>${agg['total_usd']:.4f}</b> spent · <b>{agg['total_tok']:,}</b> tok</span></div>
</body></html>"""


def render_card(run_id: str, out_path: Optional[str] = None, width: int = 720,
                html2png: Optional[str] = None) -> Optional[str]:
    """Render the run card to a PNG. Needs a Puppeteer-style ``html2png.js`` helper
    (``node html2png.js <html> <png> <width> <minHeight>``); supply its path via the
    ``html2png`` arg or the ``FLOW_HTML2PNG`` env var. Returns the PNG path, or None
    if rendering is unavailable (degrades gracefully — never raises)."""
    import os
    import subprocess
    import tempfile
    html = card_html(run_id)
    if html is None:
        return None
    script = html2png or os.environ.get("FLOW_HTML2PNG")
    if not script or not os.path.exists(script):
        return None
    if out_path is None:
        out_path = str(run_dir_for(run_id) / "card.png")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        html_path = f.name
    try:
        proc = subprocess.run(["node", script, html_path, out_path, str(width), "120"],
                              capture_output=True, text=True, timeout=60, env=dict(os.environ))
        if proc.returncode == 0 and os.path.exists(out_path):
            return out_path
    except Exception:
        pass
    finally:
        try:
            os.unlink(html_path)
        except OSError:
            pass
    return None


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
