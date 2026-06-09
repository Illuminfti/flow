"""v3 live: real Codex + Claude Code harnesses as agent leaves.

Run explicitly:  pytest tests/test_live_agents.py -m live
Needs: `codex` CLI (ChatGPT subscription) and/or `claude` CLI on PATH.
"""
import json
import shutil
import subprocess

import pytest

from flow import config
from flow.runtime import run_workflow

pytestmark = pytest.mark.live

VERDICT = {"type": "object", "required": ["verdict", "issues"],
           "properties": {"verdict": {"type": "string"},
                          "issues": {"type": "array", "items": {"type": "string"}}},
           "additionalProperties": False}


def _cfg(tmp_path, monkeypatch):
    cfg = {
        "engine": {"executor": "thread"},
        "leaf": {"allow_light_models": True, "noise_patterns": []},
        "providers": {}, "models": {},
        "tiers": {"quality": [], "local": []},
        "agents": {
            "coder": {"harness": "codex", "sandbox": "workspace-write",
                      "reserve_tokens": 30000, "timeout_s": 600},
            "reviewer": {"harness": "claude", "model": "sonnet",
                         "allowed_tools": ["Read", "Grep", "Glob"],
                         "reserve_tokens": 30000, "timeout_s": 600},
        },
    }
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(cfg))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_codex_agent_writes_code_in_worktree(tmp_path, monkeypatch):
    if not shutil.which("codex"):
        pytest.skip("codex CLI not on PATH")
    _cfg(tmp_path, monkeypatch)
    repo = _git_repo(tmp_path)

    def run(wf, args):
        wf.phase("build")
        return wf.code(
            "Create a file fizzbuzz.py containing a fizzbuzz(n) function that "
            "returns a list of the classic fizzbuzz strings for 1..n. "
            'When done, reply with JSON {"verdict": "done"}.',
            agent="coder", workspace=str(repo), isolation="worktree",
            schema=VERDICT, label="fizzbuzz")

    rep = run_workflow(run_fn=run, args={}, run_id="live-agent-codex-001",
                       script_id="s", slug="t",
                       budget={"max_tokens": 200000, "max_calls": 5})
    assert rep["status"] == "completed", rep.get("error")
    receipt = rep["final"]
    assert receipt["value"]["verdict"] == "done"
    assert receipt["patch"]["changed"] is True
    assert any("fizzbuzz" in f for f in receipt["patch"]["files"])
    assert receipt["session_id"]
    # real usage, not estimated
    assert rep["spend"]["input_tokens"] > 1000
    # the live repo itself untouched
    assert not (repo / "fizzbuzz.py").exists()


def test_claude_reviewer_agent_reads_workspace(tmp_path, monkeypatch):
    if not shutil.which("claude"):
        pytest.skip("claude CLI not on PATH")
    _cfg(tmp_path, monkeypatch)
    repo = _git_repo(tmp_path)
    (repo / "buggy.py").write_text("def add(a, b):\n    return a - b\n")

    def run(wf, args):
        wf.phase("review")
        return wf.code(
            "Read buggy.py in this directory. Does add() do what its name "
            'says? Reply with JSON {"verdict": "accept"|"reject", "issues": [...]}.',
            agent="reviewer", workspace=str(repo), schema=VERDICT, label="review")

    rep = run_workflow(run_fn=run, args={}, run_id="live-agent-claude-001",
                       script_id="s", slug="t",
                       budget={"max_tokens": 200000, "max_calls": 5})
    assert rep["status"] == "completed", rep.get("error")
    receipt = rep["final"]
    assert receipt["value"]["verdict"] == "reject"  # a-b is not addition
    assert receipt["session_id"]
    assert rep["spend"]["usd"] < 0.50
