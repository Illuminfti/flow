import os
import time

from flowleaf.runtime import run_workflow


def _sleep_then(val, secs=0.3):
    time.sleep(secs)
    return val


def _pid_fn():
    time.sleep(0.5)  # hold the worker so the pool must spread across processes
    return os.getpid()


def test_parallel_is_actually_concurrent():
    N = 10

    def run(wf, args):
        wf.phase("fan")
        t0 = time.time()
        out = wf.parallel([(lambda i=i: wf.local(_sleep_then, i, 0.3, label=f"s{i}")) for i in range(N)])
        return {"wall": time.time() - t0, "out": out}

    rep = run_workflow(run_fn=run, max_workers=N, slug="conc")
    assert rep["status"] == "completed"
    assert sorted(rep["final"]["out"]) == list(range(N))
    assert rep["final"]["wall"] < 1.5, rep["final"]["wall"]


def test_process_mode_distinct_pids():
    def run(wf, args):
        wf.phase("pids")
        return wf.parallel([(lambda i=i: int(wf.local(_pid_fn, label=f"p{i}"))) for i in range(4)])

    rep = run_workflow(run_fn=run, executor_kind="process", max_workers=4, slug="proc")
    pids = set(rep["final"])
    assert len(pids) >= 2 and os.getpid() not in pids


def test_call_budget_halts():
    def big(_):
        return "x" * 100

    def run(wf, args):
        wf.phase("spend")
        for i in range(20):
            wf.local(big, label=f"b{i}")
        return "done"

    rep = run_workflow(run_fn=run, budget={"max_calls": 5}, slug="cap")
    assert rep["status"] == "failed"
    assert rep["spend"]["calls"] <= 5


def test_nested_workflow_shares_pool():
    def child(wf, n):
        return wf.parallel([(lambda i=i: wf.local(_sleep_then, i, 0.1, label=f"c{i}")) for i in range(n)])

    def run(wf, args):
        wf.phase("parent")
        return {"a": wf.workflow(child, 3, label="a"), "b": wf.workflow(child, 2, label="b")}

    rep = run_workflow(run_fn=run, slug="nest")
    assert rep["status"] == "completed"
    assert sorted(rep["final"]["a"]) == [0, 1, 2]
    assert sorted(rep["final"]["b"]) == [0, 1]


def test_resume_reuses_completed_leaves():
    run_id = "resume-fixed-001"
    side = {"n": 0}

    def counting(i):
        side["n"] += 1
        return i

    def run(wf, args):
        wf.phase("p")
        return wf.parallel([(lambda i=i: wf.local(counting, i, label=f"r{i}")) for i in range(5)])

    rep1 = run_workflow(run_fn=run, run_id=run_id, slug="resume", script_id="fixed")
    assert sorted(rep1["final"]) == [0, 1, 2, 3, 4]
    first = side["n"]
    rep2 = run_workflow(run_fn=run, run_id=run_id, slug="resume", script_id="fixed")
    assert sorted(rep2["final"]) == [0, 1, 2, 3, 4]
    assert side["n"] == first
    assert rep2["resumed"] == 5
