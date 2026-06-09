"""WS-A: durable budget-replay-on-resume (plan §3) + manifest rehydration (§11.2)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from flow import config
from flow.runtime import run_workflow
from tests import _budget_crash_helpers as bh

CFG = {
    "engine": {"executor": "thread"},
    "leaf": {"allow_light_models": True, "noise_patterns": []},
    "providers": {"sh": {"kind": "shell_cmd", "cmd_template": ["echo", "{prompt}"]}},
    "models": {"echo": {"provider": "sh", "id": "echo", "in": 0.0, "out": 0.0,
                        "free": True, "caps": []}},
    "tiers": {"quality": ["echo"], "local": []},
    "defaults": {"tier": "quality"},
}


def test_budget_replay_on_resume(tmp_path, monkeypatch):
    data = tmp_path / "data"
    marker = tmp_path / "crashed"
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(CFG))
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src"),
           "FLOW_DATA_DIR": str(data), "FLOW_CONFIG": str(cfgp)}
    proc = subprocess.run(
        [sys.executable, "-m", "tests._budget_crash_helpers", str(marker)],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 137, proc.stderr
    assert marker.exists()

    run_dir = data / "runs" / bh.RUN_ID
    pre = {p.stem: json.loads(p.read_text(encoding="utf-8"))
           for p in (run_dir / "leaves").glob("*.json")}
    completed_pre = {lid: r for lid, r in pre.items() if r["status"] == "completed"}
    assert len(completed_pre) == 2
    spent_pre = sum(r["input_tokens"] + r["output_tokens"] for r in completed_pre.values())
    assert spent_pre > 0

    monkeypatch.setenv("FLOW_DATA_DIR", str(data))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)
    # budget=None: the ceiling must come from the persisted manifest, not the caller.
    rep = run_workflow(run_fn=bh.build_run(str(marker)), run_id=bh.RUN_ID,
                       script_id=bh.SCRIPT_ID, slug="budget", max_workers=1)

    records = [json.loads(l) for l in
               (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    replays = [r for r in records if r.get("event") == "budget_replay"]
    assert len(replays) == 1
    rs = replays[0]["replayed_spend"]
    assert replays[0]["leaves"] == 2
    assert rs["input_tokens"] + rs["output_tokens"] == spent_pre
    assert rs["calls"] == 2

    assert rep["resumed"] == 2
    # Total committed across both process lifetimes ≤ max_tokens (pre-fix: ~456 > 400).
    assert rep["spend"]["tokens"] <= bh.MAX_TOKENS
    # Replayed spend makes the ceiling bind on the second lifetime's tail leaf.
    assert rep["status"] == "failed"
    assert "token budget would breach" in rep["error"]
    # Ceiling rehydrated from the manifest (None would report remaining tokens=None).
    assert rep["remaining"]["tokens"] == bh.MAX_TOKENS - rep["spend"]["tokens"]


def test_resume_rehydrates_manifest_args(tmp_path):
    def run(wf, args):
        wf.phase("p")
        return wf.local(lambda n: n * 2, args["n"], label="double")

    rep1 = run_workflow(run_fn=run, args={"n": 21}, run_id="args-rehydrate-001",
                        script_id="s", slug="t")
    assert rep1["status"] == "completed"
    assert rep1["final"] == 42

    # Resume without args: pre-fix this crashed with 'NoneType' object is not
    # subscriptable (observed on tool-202625-5382); the manifest now supplies args.
    rep2 = run_workflow(run_fn=run, run_id="args-rehydrate-001",
                        script_id="s", slug="t")
    assert rep2["status"] == "completed"
    assert rep2["final"] == 42
    assert rep2["resumed"] == 1
