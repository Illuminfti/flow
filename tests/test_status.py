"""Phase 2A: inspectable resume state (flow status + resume plan)."""
import subprocess
import sys
from pathlib import Path

from flow import progress
from flow.runtime import run_workflow
from tests import _crash_helpers as ch


def _sq(x):
    return x * x


def test_status_report_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("go")
        return wf.parallel([(lambda i=i: wf.local(_sq, i, label=f"n{i}")) for i in range(3)])

    run_workflow(run_fn=run, run_id="st-1", slug="st")
    rep = progress.status_report("st-1")
    assert rep["count"] == 3 and rep["failed"] == 0
    assert all(n["status"] == "completed" for n in rep["nodes"].values())
    assert rep["resume"]["skip"] == 3 and rep["resume"]["rerun"] == 0
    txt = progress.render_status(rep)
    assert "flow status" in txt and "resume impact" in txt


def test_resume_plan_after_crash(tmp_path):
    data = tmp_path / "data"
    sentinel = tmp_path / "s.txt"
    marker = tmp_path / "m"
    proc = subprocess.run(
        [sys.executable, "-m", "tests._crash_helpers", str(data), str(sentinel), "2", str(marker)],
        cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "FLOW_DATA_DIR": str(data)},
    )
    assert proc.returncode == 137
    import os
    os.environ["FLOW_DATA_DIR"] = str(data)
    try:
        plan = progress.resume_plan(ch.RUN_ID)
        # leaves 0,1 completed before crash -> skip; leaf 2 started-only -> rerun
        labels = {lid: progress._aggregate(ch.RUN_ID)["leaves"][lid]["label"] for lid in plan["plan"]}
        by_label = {labels[lid]: plan["plan"][lid] for lid in plan["plan"]}
        assert by_label.get("k0") == "skip" and by_label.get("k1") == "skip"
        assert by_label.get("k2") == "rerun"
        assert plan["skip"] == 2 and plan["rerun"] >= 1
    finally:
        os.environ.pop("FLOW_DATA_DIR", None)


def test_status_backward_compat_no_state_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        return wf.local(_sq, 5, label="x")

    run_workflow(run_fn=run, run_id="bc-1", slug="bc")
    # status_report rebuilds purely from journal.jsonl (no separate state file needed)
    assert progress.status_report("bc-1")["count"] == 1
