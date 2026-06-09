"""Feature 2 tests — flow init agents: section detection."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from flow._bootstrap import cmd_init


def _read_config(path: Path) -> dict:
    if path.suffix == ".yaml":
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Both CLIs present
# ---------------------------------------------------------------------------

def test_init_agents_both_detected(tmp_path, monkeypatch):
    """When codex and claude are both on PATH the config gets an agents section with both."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("FLOW_CONFIG", str(cfg_path))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    # force=True so we write fresh
    with patch("shutil.which", side_effect=lambda cli: f"/usr/bin/{cli}" if cli in ("codex", "claude") else None):
        ret = cmd_init(force=True)
    assert ret == 0
    cfg = _read_config(cfg_path)
    assert "agents" in cfg
    agents = cfg["agents"]
    assert "coder" in agents
    assert agents["coder"]["harness"] == "codex"
    assert agents["coder"]["reserve_tokens"] == 30000
    assert "reviewer" in agents
    assert agents["reviewer"]["harness"] == "claude"
    assert agents["reviewer"]["model"] == "sonnet"
    assert set(agents["reviewer"]["allowed_tools"]) == {"Read", "Grep", "Glob"}
    assert agents["reviewer"]["reserve_tokens"] == 30000


# ---------------------------------------------------------------------------
# Neither CLI present
# ---------------------------------------------------------------------------

def test_init_agents_none_detected(tmp_path, monkeypatch):
    """When no coding-agent CLI is on PATH the agents key is absent."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("FLOW_CONFIG", str(cfg_path))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    with patch("shutil.which", return_value=None):
        ret = cmd_init(force=True)
    assert ret == 0
    cfg = _read_config(cfg_path)
    assert "agents" not in cfg


# ---------------------------------------------------------------------------
# Only codex present
# ---------------------------------------------------------------------------

def test_init_agents_codex_only(tmp_path, monkeypatch):
    """Only codex on PATH → only coder in agents, no reviewer."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("FLOW_CONFIG", str(cfg_path))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    with patch("shutil.which", side_effect=lambda cli: "/usr/bin/codex" if cli == "codex" else None):
        cmd_init(force=True)
    cfg = _read_config(cfg_path)
    assert "agents" in cfg
    assert "coder" in cfg["agents"]
    assert "reviewer" not in cfg["agents"]


# ---------------------------------------------------------------------------
# Only claude present
# ---------------------------------------------------------------------------

def test_init_agents_claude_only(tmp_path, monkeypatch):
    """Only claude on PATH → only reviewer in agents, no coder."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("FLOW_CONFIG", str(cfg_path))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    with patch("shutil.which", side_effect=lambda cli: "/usr/bin/claude" if cli == "claude" else None):
        cmd_init(force=True)
    cfg = _read_config(cfg_path)
    assert "agents" in cfg
    assert "reviewer" in cfg["agents"]
    assert "coder" not in cfg["agents"]


# ---------------------------------------------------------------------------
# Existing config with agents is preserved
# ---------------------------------------------------------------------------

def test_init_agents_preserves_existing(tmp_path, monkeypatch, capsys):
    """If config already exists with an agents section, it is NOT overwritten."""
    existing_agents = {"my_custom_agent": {"harness": "custom", "reserve_tokens": 99999}}
    existing_cfg = {
        "engine": {}, "leaf": {}, "providers": {}, "models": {},
        "tiers": {}, "defaults": {}, "agents": existing_agents,
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(existing_cfg), encoding="utf-8")
    monkeypatch.setenv("FLOW_CONFIG", str(cfg_path))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))

    # force=False (default) → existing config left alone
    with patch("shutil.which", side_effect=lambda cli: f"/usr/bin/{cli}" if cli in ("codex", "claude") else None):
        ret = cmd_init(force=False)
    assert ret == 0
    cfg = _read_config(cfg_path)
    # agents key must still be the original custom one
    assert cfg["agents"] == existing_agents
    out = capsys.readouterr().out
    assert "already exists" in out


# ---------------------------------------------------------------------------
# force=True on existing config with agents replaces it
# ---------------------------------------------------------------------------

def test_init_agents_force_overwrites(tmp_path, monkeypatch):
    """--force on existing config regenerates it (including new agents section)."""
    old_cfg = {"engine": {}, "agents": {"old": {"harness": "old_harness"}}}
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(old_cfg), encoding="utf-8")
    monkeypatch.setenv("FLOW_CONFIG", str(cfg_path))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    with patch("shutil.which", side_effect=lambda cli: "/usr/bin/codex" if cli == "codex" else None):
        cmd_init(force=True)
    cfg = _read_config(cfg_path)
    # "old" key is gone — regenerated from _CONFIG + detected agents
    assert "old" not in cfg.get("agents", {})
    assert cfg["agents"]["coder"]["harness"] == "codex"
