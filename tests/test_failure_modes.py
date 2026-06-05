"""Phase 1B: explicit parallel/pipeline failure modes."""

from flow import ExecutionResult, ParallelError
from flow.runtime import run_workflow


def _boom(_x):
    raise ValueError("boom")


def test_lenient_default_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("p")
        return wf.parallel([
            (lambda: wf.local(lambda: 1, label="a")),
            (lambda: wf.local(_boom, 0, label="b")),  # raises -> None
            (lambda: wf.local(lambda: 3, label="c")),
        ])

    rep = run_workflow(run_fn=run, slug="len")
    assert rep["final"] == [1, None, 3]


def test_fail_fast_raises_parallel_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("p")
        return wf.parallel([
            (lambda: wf.local(lambda: 1, label="a")),
            (lambda: wf.local(_boom, 0, label="b")),
            (lambda: wf.local(lambda: 3, label="c")),
        ], mode="fail_fast")

    rep = run_workflow(run_fn=run, slug="ff")
    assert rep["status"] == "failed"
    assert "item" in (rep.get("error") or "")


def test_collect_errors_envelopes(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("p")
        res = wf.parallel([
            (lambda: wf.local(lambda: 1, label="a")),
            (lambda: wf.local(_boom, 0, label="b")),
        ], mode="collect_errors")
        return [(r.ok, r.value, bool(r.error), r.index) for r in res]

    rep = run_workflow(run_fn=run, slug="ce")
    assert rep["final"] == [(True, 1, False, 0), (False, None, True, 1)]


def test_pipeline_collect_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("p")
        res = wf.pipeline([1, 2], (lambda prev, o, i: wf.local(_boom, prev, label=f"s{i}")),
                          mode="collect_errors")
        return [r.ok for r in res]

    rep = run_workflow(run_fn=run, slug="pce")
    assert rep["final"] == [False, False]


def test_execution_result_and_parallel_error_exported():
    assert ExecutionResult(True, 1).ok is True
    e = ParallelError(2, ValueError("x"))
    assert e.index == 2 and isinstance(e.cause, ValueError)
