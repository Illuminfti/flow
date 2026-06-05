"""Power-upgrade tests: P1 retry/backoff."""
from flow import leaves
from flow.backends.base import BackendError, BackendResponse
from flow.runtime import run_workflow


def _factory(seq):
    """seq: list of either BackendError (raise) or str (return text)."""
    state = {"i": 0}

    def factory(req):
        def backend(prompt, *, timeout=None):
            item = seq[min(state["i"], len(seq) - 1)]
            state["i"] += 1
            if isinstance(item, BackendError):
                raise item
            return BackendResponse(item, 5, 5)
        return backend
    factory.state = state
    return factory


def test_retries_transient_then_succeeds(monkeypatch):
    f = _factory([BackendError("503", status=503), BackendError("busy", status=503), "ok"])
    monkeypatch.setattr(leaves, "build_backend", f)

    def run(wf, args):
        wf.phase("p")
        return wf.agent("q", label="q", tier="quality")

    rep = run_workflow(run_fn=run, slug="retry")
    assert rep["status"] == "completed"
    assert rep["final"] == "ok"
    assert f.state["i"] == 3  # 2 retries then success


def test_no_retry_on_auth_error(monkeypatch):
    f = _factory([BackendError("unauthorized", status=401), "should-not-reach"])
    monkeypatch.setattr(leaves, "build_backend", f)

    def run(wf, args):
        wf.phase("p")
        return wf.agent("q", label="q", tier="quality", required=False)

    rep = run_workflow(run_fn=run, slug="auth")
    assert rep["final"] is None  # failed
    assert f.state["i"] == 1  # no retry on 401


def test_retryable_status_classification():
    assert BackendError("x", status=429).retryable is True
    assert BackendError("x", status=503).retryable is True
    assert BackendError("x", status=401).retryable is False
    assert BackendError("x", retryable=True).retryable is True
    assert BackendError("x").retryable is False
