"""WS-B: the wf.loop envelope (plan §1/§2) — LoopSpec/LoopRun/IterationRecord/LoopLedger."""
import json
import os
import subprocess
import sys
from pathlib import Path

from flow import config
from flow.loop import LoopSpec, VerifierIdentityError
from flow.paths import run_dir_for
from flow.runtime import run_workflow
from tests import _loop_crash_helpers as lh

ECHO_CFG = {
    "engine": {"executor": "thread"},
    "leaf": {"allow_light_models": True, "noise_patterns": []},
    "providers": {"sh": {"kind": "shell_cmd", "cmd_template": ["echo", "{prompt}"]}},
    "models": {"echo": {"provider": "sh", "id": "echo", "in": 0.0, "out": 0.0,
                        "free": True, "caps": []}},
    "tiers": {"quality": ["echo"], "local": []},
    "defaults": {"tier": "quality"},
}


def _journal(run_id):
    jp = run_dir_for(run_id) / "journal.jsonl"
    return [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()]


def _step(wf, ctx):
    return wf.local(lambda i, p: {"i": i, "prev": p}, ctx["iteration"], ctx["prev"],
                    label=f"step{ctx['iteration']}")


def _verify(wf, ctx):
    return wf.local(lambda c: {"verdict": "accept", "i": c["i"]}, ctx["candidate"],
                    label=f"verify{ctx['iteration']}")


def _loop_run(spec):
    def run(wf, args):
        wf.phase("work")
        return wf.loop(spec=spec, step=_step, verify=_verify)
    return run


def test_loop_runs_n_iterations():
    rep = run_workflow(run_fn=_loop_run(LoopSpec(goal="g", max_iterations=3)),
                       args={}, run_id="loop-n-001", script_id="s", slug="t")
    assert rep["status"] == "completed"
    final = rep["final"]
    assert final["status"] == "completed"
    assert final["stop_reason"] == "max_iterations"
    assert final["iterations"] == 3
    assert final["replayed"] == 0
    assert final["candidate"] == {"i": 3, "prev": {"i": 2, "prev": {"i": 1, "prev": None}}}
    recs = final["records"]
    assert [r["iteration"] for r in recs] == [1, 2, 3]
    assert all(r["status"] == "completed" for r in recs)
    assert all(r["verifier_verdict"] == {"verdict": "accept", "i": r["iteration"]} for r in recs)
    assert all(r["context_hash"] for r in recs)
    assert len({r["context_hash"] for r in recs}) == 3
    assert all(r["spend"]["calls"] == 2 for r in recs)  # step + verify leaves
    assert all(len(r["leaves"]) == 2 for r in recs)


def test_iteration_records_in_wal():
    run_id = "loop-wal-001"
    run_workflow(run_fn=_loop_run(LoopSpec(goal="g", max_iterations=2)),
                 args={}, run_id=run_id, script_id="s", slug="t")
    records = _journal(run_id)
    events = [r["event"] for r in records]
    assert events.count("loop_started") == 1
    assert events.count("iteration_started") == 2
    assert events.count("iteration_completed") == 2
    assert events.count("loop_stopped") == 1
    loop_id = next(r["loop_id"] for r in records if r["event"] == "loop_started")
    run_dir = run_dir_for(run_id)
    for r in (r for r in records if r["event"] == "iteration_completed"):
        assert r["loop_id"] == loop_id
        rec = json.loads((run_dir / r["record_ref"]).read_text(encoding="utf-8"))
        assert rec["iteration"] == r["iteration"]
        assert rec["loop_id"] == loop_id
        assert rec["status"] == "completed"
        assert rec["leaves"] and rec["spend"]["calls"] == 2
    stopped = next(r for r in records if r["event"] == "loop_stopped")
    assert stopped["stop_reason"] == "max_iterations"
    assert stopped["iterations"] == 2
    # started precedes completed per iteration (fsync WAL ordering)
    for n in (1, 2):
        s = next(r["seq"] for r in records
                 if r["event"] == "iteration_started" and r["iteration"] == n)
        c = next(r["seq"] for r in records
                 if r["event"] == "iteration_completed" and r["iteration"] == n)
        assert s < c


def test_crash_mid_iteration_resume_skips_completed(tmp_path, monkeypatch):
    data = tmp_path / "data"
    marker = tmp_path / "crashed"
    exec_log = tmp_path / "exec.log"
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src"),
           "FLOW_DATA_DIR": str(data), "FLOW_CONFIG": str(cfgp)}
    proc = subprocess.run(
        [sys.executable, "-m", "tests._loop_crash_helpers", str(marker), str(exec_log)],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 137, proc.stderr
    assert marker.exists()
    assert exec_log.read_text().splitlines() == ["step1", "step2", "step3"]

    monkeypatch.setenv("FLOW_DATA_DIR", str(data))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)
    rep = run_workflow(run_fn=lh.build_run(str(marker), str(exec_log)),
                       run_id=lh.RUN_ID, script_id=lh.SCRIPT_ID, slug="loop", max_workers=2)
    assert rep["status"] == "completed"
    final = rep["final"]
    assert final["iterations"] == 4
    assert final["replayed"] == 2
    assert final["stop_reason"] == "max_iterations"
    assert final["candidate"] == 40
    # Completed iterations 1–2 never re-executed step(); iteration 3 (mid-crash) reran.
    lines = exec_log.read_text().splitlines()
    assert lines == ["step1", "step2", "step3", "step3", "step4"]

    records = _journal(lh.RUN_ID)
    replays = [r["iteration"] for r in records if r["event"] == "iteration_replayed"]
    assert sorted(replays) == [1, 2]
    assert sum(1 for r in records if r["event"] == "iteration_completed") == 4
    assert sum(1 for r in records if r["event"] == "loop_started") == 2  # both lifetimes


def test_max_iterations_stop():
    calls = []

    def step(wf, ctx):
        calls.append(ctx["iteration"])
        return wf.local(lambda i: i, ctx["iteration"], label=f"s{ctx['iteration']}")

    def run(wf, args):
        wf.phase("w")
        return wf.loop(spec=LoopSpec(goal="g", max_iterations=1), step=step)

    rep = run_workflow(run_fn=run, args={}, run_id="loop-max-001", script_id="s", slug="t")
    final = rep["final"]
    assert calls == [1]
    assert final["iterations"] == 1
    assert final["stop_reason"] == "max_iterations"
    assert final["status"] == "completed"
    assert final["records"][0]["verifier_verdict"] is None  # verify omitted


def test_budget_exhausted_stop_loop_budget(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)

    def step(wf, ctx):
        return wf.agent("pad " * 40 + str(ctx["iteration"]), label=f"s{ctx['iteration']}",
                        model="echo", max_tokens=40)

    def run(wf, args):
        wf.phase("w")
        return wf.loop(spec=LoopSpec(goal="g", max_iterations=10,
                                     budget={"max_tokens": 150}), step=step)

    rep = run_workflow(run_fn=run, args={}, run_id="loop-budget-001", script_id="s",
                       slug="t", budget={"max_tokens": 100000})
    final = rep["final"]
    assert final["stop_reason"] == "budget_exhausted"
    assert final["status"] == "exhausted"
    assert 1 <= final["iterations"] < 10
    assert final["spend"]["tokens"] >= 150
    assert all(r["status"] == "completed" for r in final["records"])
    stopped = next(r for r in _journal("loop-budget-001") if r["event"] == "loop_stopped")
    assert stopped["stop_reason"] == "budget_exhausted"


def test_budget_exhausted_stop_engine_budget(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)

    def step(wf, ctx):
        return wf.agent("pad " * 40 + str(ctx["iteration"]), label=f"s{ctx['iteration']}",
                        model="echo", max_tokens=40)

    def run(wf, args):
        wf.phase("w")
        return wf.loop(spec=LoopSpec(goal="g", max_iterations=10), step=step)

    rep = run_workflow(run_fn=run, args={}, run_id="loop-budget-002", script_id="s",
                       slug="t", budget={"max_tokens": 120})
    final = rep["final"]
    assert final["stop_reason"] == "budget_exhausted"
    assert final["iterations"] < 10
    failed = final["records"][-1]
    assert failed["status"] == "failed"
    assert any(str(f.get("error") or "").startswith("budget:") for f in failed["failures"])
    # The breach is recorded, not silently retried until max_iterations.
    assert rep["spend"]["tokens"] <= 120


def test_verifier_identity_fail_closed():
    spec = LoopSpec(goal="g", max_iterations=2,
                    verifier_policy={"tier": "quality", "must_differ_from_executor": True})
    seen = {}

    def run(wf, args):
        wf.phase("work")
        try:
            return wf.loop(spec=spec, step=_step, verify=_verify)
        except VerifierIdentityError as exc:
            seen["type"] = type(exc).__name__
            raise

    rep = run_workflow(run_fn=run, args={}, run_id="loop-vid-001",
                       script_id="s", slug="t")
    assert seen["type"] == "VerifierIdentityError"
    assert rep["status"] == "failed"
    assert "must not self-grade" in rep["error"]
    records = _journal("loop-vid-001")
    assert any(r["event"] == "verifier_identity_collision" for r in records)
    assert not any(r["event"] == "iteration_started" for r in records)


def test_verifier_identity_distinct_tier_passes():
    spec = LoopSpec(goal="g", max_iterations=1,
                    verifier_policy={"tier": "cheap", "must_differ_from_executor": True})
    rep = run_workflow(run_fn=_loop_run(spec), args={}, run_id="loop-vid-002",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    assert rep["final"]["iterations"] == 1


def test_v1_workflows_unchanged():
    """Regression lock (plan §5.2): a v1 DAG runs with no loop machinery in its
    journal, run dir, or report surface."""
    from flow.templates import selftest

    run_id = "v1-lock-001"
    rep = run_workflow(run_fn=selftest.run, args={"n": 4}, run_id=run_id,
                       script_id="selftest", slug="t")
    assert rep["status"] == "completed"
    assert rep["final"]["squares"] == [0, 1, 4, 9]
    assert rep["final"]["doubled"] == [0, 2, 8, 18]
    assert rep["final"]["nested"] == [1, 4, 9]
    assert set(rep) == {
        "status", "run_dir", "leaf_count", "resumed", "failed_count",
        "required_failed_count", "self_healed_count", "warning_count", "spend",
        "remaining", "phases", "final", "finalization_error", "failed",
        "required_failed", "warnings", "self_healed", "artifacts",
    }
    events = {r["event"] for r in _journal(run_id)}
    assert events == {"phase", "routed", "started", "completed"}
    run_dir = run_dir_for(run_id)
    assert not (run_dir / "iterations").exists()
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "journal.jsonl", "leaves", "manifest.json", "outbox", "run-report.json", "state.json",
    ]
