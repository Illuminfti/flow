"""Phase 1A: cancellation + deadline cascade."""
import threading
import time

from flow.budget import Budget
from flow.paths import run_dir_for
from flow.runtime import Workflow, run_workflow
from flow.scheduler import Engine
from flow import progress


def test_cancel_skips_pending_and_reruns_on_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    rd = run_dir_for("cancel-resume-1")
    counter: dict = {}

    def work(i):
        counter[i] = counter.get(i, 0) + 1
        return i

    eng = Engine(run_dir=rd, budget=Budget(), manifest={"run_id": "cancel-resume-1"})
    wf = Workflow(eng, script_id="c")
    for i in range(3):
        wf.local(work, i, label=f"k{i}")          # 0,1,2 complete
    eng._cancel.set()                              # cancel the run
    for i in (3, 4):
        try:
            wf.local(work, i, label=f"k{i}")       # cancelled -> wf.local raises
        except RuntimeError:
            pass
    eng.shutdown()
    assert counter == {0: 1, 1: 1, 2: 1}           # 3,4 never executed

    # Resume: completed leaves skipped, cancelled leaves re-run.
    eng2 = Engine(run_dir=rd, budget=Budget(), manifest={"run_id": "cancel-resume-1"})
    assert eng2.resumed_count == 3                  # 0,1,2 loaded from journal
    wf2 = Workflow(eng2, script_id="c")
    for i in range(5):
        wf2.local(work, i, label=f"k{i}")
    eng2.shutdown()
    # 0,1,2 not re-run (still 1); 3,4 ran on resume (now 1)
    assert counter == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1}


def test_deadline_cascade_sets_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("p")
        out = []
        for i in range(5):
            try:
                out.append(wf.local(time.sleep, 0.1, label=f"s{i}"))
            except RuntimeError:
                out.append("cancelled")
        return out

    rep = run_workflow(run_fn=run, run_id="dl-1", budget={"deadline_seconds": 0.05},
                       max_workers=1, slug="dl")
    agg = progress._aggregate("dl-1")
    statuses = {e["status"] for e in agg["leaves"].values()}
    assert "cancelled" in statuses or rep["status"] == "failed"


def test_wf_cancelled_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    eng = Engine(run_dir=run_dir_for("flag-1"), budget=Budget(), manifest={})
    wf = Workflow(eng, script_id="f")
    assert wf.cancelled() is False
    eng._cancel.set()
    assert wf.cancelled() is True
    eng.shutdown()


def test_signal_handler_off_main_thread_no_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    result = {}

    def run(wf, args):
        return wf.local(lambda: 1, label="x")

    def go():
        result["rep"] = run_workflow(run_fn=run, slug="thr")

    t = threading.Thread(target=go)
    t.start()
    t.join()
    assert result["rep"]["status"] == "completed"  # no ValueError from signal.signal
