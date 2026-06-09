"""v3: agentic leaves — wf.code, agent_cli backend, worktree isolation."""
import json
import subprocess

import pytest

from flow import config
from flow.paths import run_dir_for
from flow.runtime import run_workflow
from tests._fake_harness import write_fake

ANSWER_SCHEMA = {"type": "object", "required": ["ok"],
                 "properties": {"ok": {"type": "boolean"}},
                 "additionalProperties": False}


def _agents_cfg(tmp_path, monkeypatch, **agent_overrides):
    cfg = {
        "engine": {"executor": "thread"},
        "leaf": {"allow_light_models": True, "noise_patterns": []},
        "providers": {}, "models": {},
        "tiers": {"quality": [], "local": []},
        "agents": {
            "coder": {"harness": "codex", "bin": write_fake(tmp_path, "codex"),
                      "reserve_tokens": 2000, **agent_overrides},
            "reviewer": {"harness": "claude", "bin": write_fake(tmp_path, "claude"),
                         "model": "sonnet", "reserve_tokens": 2000},
        },
    }
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(cfg))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    monkeypatch.setenv("FAKE_STATE_DIR", str(tmp_path / "fake-state"))
    config.get(reload=True)
    return tmp_path / "fake-state"


def _calls(state_dir):
    log = state_dir / "calls.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def test_codex_agent_leaf_runs_in_workspace(tmp_path, monkeypatch):
    state = _agents_cfg(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()

    def run(wf, args):
        wf.phase("build")
        return wf.code("write the file", agent="coder", workspace=str(ws),
                       schema=ANSWER_SCHEMA, label="task1")

    rep = run_workflow(run_fn=run, args={}, run_id="agent-codex-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    receipt = rep["final"]
    assert receipt["value"] == {"ok": True}
    assert receipt["session_id"] == "fake-thread-1"
    assert receipt["agent"] == "coder"
    # the harness really ran in the workspace
    assert (ws / "agent-was-here.txt").exists()
    # REAL usage committed, not estimated: 1000 in + (50+10) out
    assert rep["spend"]["input_tokens"] >= 1000
    assert rep["spend"]["output_tokens"] >= 60
    # transcript persisted under the run dir
    transcripts = list((run_dir_for("agent-codex-001") / "agents").glob("*.transcript.jsonl"))
    assert transcripts and "thread.started" in transcripts[0].read_text()
    calls = _calls(state)
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(ws)
    assert "--output-schema" in calls[0]["argv"]  # codex native schema enforcement
    assert "-s" in calls[0]["argv"]               # sandboxed by default


def test_claude_agent_leaf_envelope(tmp_path, monkeypatch):
    _agents_cfg(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()

    def run(wf, args):
        wf.phase("review")
        return wf.code("review it", agent="reviewer", workspace=str(ws),
                       schema=ANSWER_SCHEMA, label="rev1")

    rep = run_workflow(run_fn=run, args={}, run_id="agent-claude-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    receipt = rep["final"]
    assert receipt["value"] == {"ok": True}
    assert receipt["session_id"] == "fake-session-1"
    # input = input + cache_creation + cache_read = 700+200+100
    assert rep["spend"]["input_tokens"] == 1000
    assert rep["spend"]["usd"] == pytest.approx(0.012)


def test_agent_schema_repair_uses_continuation(tmp_path, monkeypatch):
    state = _agents_cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_MALFORMED_FIRST", "1")
    ws = tmp_path / "ws"
    ws.mkdir()

    def run(wf, args):
        wf.phase("build")
        return wf.code("write it", agent="coder", workspace=str(ws),
                       schema=ANSWER_SCHEMA, label="task1")

    rep = run_workflow(run_fn=run, args={}, run_id="agent-repair-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    assert rep["final"]["value"] == {"ok": True}
    calls = _calls(state)
    # first full call + ONE repair turn that resumed the session (cheap),
    # never a from-scratch rerun
    assert len(calls) == 2
    assert "resume" in calls[1]["argv"]
    assert "fake-thread-1" in calls[1]["argv"]


def test_worktree_isolation_parallel(tmp_path, monkeypatch):
    state = _agents_cfg(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "base.txt").write_text("base")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=repo, check=True)

    def run(wf, args):
        wf.phase("fanout")
        return wf.parallel([
            lambda i=i: wf.code(f"task {i}", agent="coder", workspace=str(repo),
                                isolation="worktree", label=f"wt{i}")
            for i in range(2)
        ])

    rep = run_workflow(run_fn=run, args={}, run_id="agent-wt-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    receipts = rep["final"]
    assert len(receipts) == 2
    for r in receipts:
        assert r["patch"]["changed"] is True
        assert "agent-was-here.txt" in r["patch"]["files"]
        patch_file = run_dir_for("agent-wt-001") / r["patch"]["patch"]
        assert patch_file.exists() and "agent-was-here" in patch_file.read_text()
    # the real repo working tree was never touched
    assert not (repo / "agent-was-here.txt").exists()
    # worktrees cleaned up
    assert subprocess.run(["git", "worktree", "list"], cwd=repo,
                          capture_output=True, text=True).stdout.count("\n") == 1
    # each agent ran in its own worktree
    cwds = {c["cwd"] for c in _calls(state)}
    assert len(cwds) == 2 and str(repo) not in cwds


def test_agent_leaf_resume_skips_completed(tmp_path, monkeypatch):
    state = _agents_cfg(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()

    def run(wf, args):
        wf.phase("build")
        return wf.code("write it", agent="coder", workspace=str(ws), label="task1")

    rep1 = run_workflow(run_fn=run, args={}, run_id="agent-resume-001",
                        script_id="s", slug="t")
    assert rep1["status"] == "completed"
    rep2 = run_workflow(run_fn=run, args={}, run_id="agent-resume-001",
                        script_id="s", slug="t")
    assert rep2["status"] == "completed"
    # a completed agentic mutation never re-executes on resume
    assert len(_calls(state)) == 1
    assert rep2["resumed"] >= 1


def test_reserve_blocks_before_invoking_agent(tmp_path, monkeypatch):
    state = _agents_cfg(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()

    def run(wf, args):
        wf.phase("build")
        return wf.code("write it", agent="coder", workspace=str(ws),
                       label="task1", required=False)

    rep = run_workflow(run_fn=run, args={}, run_id="agent-budget-001",
                       script_id="s", slug="t",
                       budget={"max_tokens": 500})  # < reserve_tokens=2000
    assert rep["final"] is None
    assert _calls(state) == []  # gated BEFORE the harness ever launched
    failed = [l for l in rep.get("failed", []) + rep.get("warnings", [])]
    assert any("budget" in str(f.get("error", "")) for f in failed)


def test_unknown_agent_raises(tmp_path, monkeypatch):
    _agents_cfg(tmp_path, monkeypatch)

    def run(wf, args):
        return wf.code("x", agent="nope", label="t")

    rep = run_workflow(run_fn=run, args={}, run_id="agent-unknown-001",
                       script_id="s", slug="t")
    assert rep["status"] == "failed"
    assert "unknown agent" in (rep.get("error") or "")


def test_generic_harness_cmd_template(tmp_path, monkeypatch):
    cfg = {
        "engine": {"executor": "thread"},
        "leaf": {"allow_light_models": True, "noise_patterns": []},
        "providers": {}, "models": {},
        "tiers": {"quality": [], "local": []},
        "agents": {"echoer": {"harness": "generic", "reserve_tokens": 10,
                              "cmd_template": ["echo", "{prompt}"]}},
    }
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(cfg))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)

    def run(wf, args):
        return wf.code("hello generic", agent="echoer", label="t",
                       workspace=str(tmp_path))

    rep = run_workflow(run_fn=run, args={}, run_id="agent-generic-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    assert rep["final"]["text"].strip() == "hello generic"


def test_continue_id_changes_identity_and_resumes_session(tmp_path, monkeypatch):
    state = _agents_cfg(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()

    def run(wf, args):
        wf.phase("build")
        first = wf.code("start the work", agent="coder", workspace=str(ws),
                        label="step1")
        follow = wf.code("now finish it", agent="coder", workspace=str(ws),
                         label="step2", continue_id=first["session_id"])
        return [first, follow]

    rep = run_workflow(run_fn=run, args={}, run_id="agent-cont-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    calls = _calls(state)
    assert len(calls) == 2
    assert "resume" not in calls[0]["argv"]
    assert "resume" in calls[1]["argv"] and "fake-thread-1" in calls[1]["argv"]
