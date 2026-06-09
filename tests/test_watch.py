"""Feature 1 tests — flow watch (hermetic, no live network)."""
from __future__ import annotations

import json


from flow import progress
from flow.paths import run_dir_for
from flow.runtime import run_workflow

# ---------------------------------------------------------------------------
# Local echo config (mirrors tests/test_loop.py ECHO_CFG pattern)
# ---------------------------------------------------------------------------
ECHO_CFG = {
    "engine": {"executor": "thread"},
    "leaf": {"allow_light_models": True, "noise_patterns": []},
    "providers": {"sh": {"kind": "shell_cmd", "cmd_template": ["echo", "{prompt}"]}},
    "models": {"echo": {"provider": "sh", "id": "echo", "in": 0.0, "out": 0.0,
                        "free": True, "caps": []}},
    "tiers": {"quality": ["echo"], "local": []},
    "defaults": {"tier": "quality"},
}


def _patch_cfg(tmp_path, monkeypatch):
    cfgp = tmp_path / "echo_cfg.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    from flow import config
    config.get(reload=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sq(x):
    return x * x


def _completed_run(run_id: str) -> str:
    """Run 3 local leaves under phase 'crunch' and return run_id."""
    def run(wf, args):
        wf.phase("crunch")
        return wf.parallel([(lambda i=i: wf.local(_sq, i, label=f"leaf{i}")) for i in range(3)])

    run_workflow(run_fn=run, run_id=run_id, slug="watch-test")
    return run_id


def _failed_run(run_id: str) -> str:
    """Run leaves where the last one raises."""
    def bad(_):
        raise RuntimeError("boom")

    def run(wf, args):
        wf.phase("crunch")
        wf.local(_sq, 1, label="ok-leaf")
        wf.local(bad, 2, label="bad-leaf")

    try:
        run_workflow(run_fn=run, run_id=run_id, slug="watch-fail")
    except Exception:
        pass
    return run_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_watch_once_completed_run(tmp_path, monkeypatch):
    """watch(once=True) on a finished run shows phase, leaf labels, ✓ glyphs, spend."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    run_id = _completed_run("w-ok-1")
    frame = progress.watch(run_id, once=True)
    assert frame is not None
    assert "crunch" in frame          # phase name
    assert "leaf0" in frame           # leaf labels
    assert "leaf1" in frame
    assert "leaf2" in frame
    assert "✓" in frame               # completed glyph
    assert "$" in frame               # spend footer / meta
    assert run_id in frame            # run id in header


def test_watch_once_failed_run(tmp_path, monkeypatch):
    """watch(once=True) on a run with a failed leaf shows ✗ glyph."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    run_id = _failed_run("w-fail-1")
    frame = progress.watch(run_id, once=True)
    assert frame is not None
    assert "✗" in frame
    assert "bad-leaf" in frame


def test_watch_once_mid_flight(tmp_path, monkeypatch):
    """watch(once=True) on a partial journal (started but not completed) shows ◌ and returns without hanging."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    run_id = "w-mid-1"
    run_dir = run_dir_for(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write a journal with a started-but-no-terminal event leaf
    journal_path = run_dir / "journal.jsonl"
    journal_path.write_text(
        json.dumps({"event": "phase", "phase": "work", "seq": 1, "ts": "2026-01-01T00:00:00Z"}) + "\n"
        + json.dumps({"event": "started", "leaf_id": "lid-1", "label": "pending-leaf",
                      "phase": "work", "seq": 2, "ts": "2026-01-01T00:00:01Z"}) + "\n",
        encoding="utf-8",
    )
    # No run-report.json → run not done

    frame = progress.watch(run_id, once=True)
    assert frame is not None
    assert "◌" in frame               # mid-flight glyph
    assert "pending-leaf" in frame
    # Function returned (did not hang)


def test_watch_once_unknown_run(tmp_path, monkeypatch):
    """watch(once=True) on a nonexistent run returns a frame without crashing."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    frame = progress.watch("nonexistent-run-xyz", once=True)
    assert frame is not None          # returns a (empty/header) frame


def test_watch_once_returns_string(tmp_path, monkeypatch):
    """watch(once=True) return type is str."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    _completed_run("w-str-1")
    result = progress.watch("w-str-1", once=True)
    assert isinstance(result, str)


def test_watch_totals_footer_spend(tmp_path, monkeypatch):
    """The frame contains a $ spend value and leaf count."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    run_id = _completed_run("w-totals-1")
    frame = progress.watch(run_id, once=True)
    # Footer line has total spend in $X.XXXX format and leaf count
    assert "leaves" in frame
    assert "$0." in frame or "$" in frame


def test_watch_cached_glyph(tmp_path, monkeypatch):
    """A cached journal event produces ↻ glyph."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    run_id = "w-cached-1"
    run_dir = run_dir_for(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    journal_path = run_dir / "journal.jsonl"
    journal_path.write_text(
        json.dumps({"event": "phase", "phase": "build", "seq": 1, "ts": "T"}) + "\n"
        + json.dumps({"event": "cached", "leaf_id": "c1", "label": "cached-leaf",
                      "phase": "build", "seq": 2, "ts": "T",
                      "tokens": {"in": 0, "out": 0}, "usd": 0.0, "elapsed_s": 0.1}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "run-report.json").write_text('{"status":"completed"}', encoding="utf-8")

    frame = progress.watch(run_id, once=True)
    assert "↻" in frame
    assert "cached-leaf" in frame


def test_watch_cancelled_glyph(tmp_path, monkeypatch):
    """A cancelled journal event produces ⊘ glyph."""
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    run_id = "w-cancel-1"
    run_dir = run_dir_for(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    journal_path = run_dir / "journal.jsonl"
    journal_path.write_text(
        json.dumps({"event": "phase", "phase": "cleanup", "seq": 1, "ts": "T"}) + "\n"
        + json.dumps({"event": "cancelled", "leaf_id": "x1", "label": "cancelled-leaf",
                      "phase": "cleanup", "seq": 2, "ts": "T",
                      "tokens": {"in": 0, "out": 0}, "usd": 0.0, "elapsed_s": 0.0}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "run-report.json").write_text('{"status":"completed"}', encoding="utf-8")

    frame = progress.watch(run_id, once=True)
    assert "⊘" in frame
    assert "cancelled-leaf" in frame
