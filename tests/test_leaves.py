"""Schema enforcement + token budget via a fake backend (no network)."""

from flowleaf import leaves
from flowleaf.backends.base import BackendResponse
from flowleaf.runtime import run_workflow

SCHEMA = {"type": "object", "required": ["title", "severity"],
          "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}


def _fake_factory(responses):
    """Return a build_backend replacement that yields canned BackendResponses."""
    state = {"i": 0}

    def factory(req):
        def backend(prompt, *, timeout=None):
            r = responses[min(state["i"], len(responses) - 1)]
            state["i"] += 1
            return r
        return backend
    return factory, state


def test_schema_one_repair_turn(monkeypatch):
    responses = [
        BackendResponse("not json", 10, 10),
        BackendResponse('{"title":"ok","severity":"high"}', 10, 10),
    ]
    factory, state = _fake_factory(responses)
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("s")
        return wf.agent("find a bug", label="bug", schema=SCHEMA, tier="quality")

    rep = run_workflow(run_fn=run, slug="schema")
    assert rep["status"] == "completed"
    assert rep["final"] == {"title": "ok", "severity": "high"}
    assert state["i"] == 2  # exactly one repair


def test_schema_double_fail(monkeypatch):
    factory, _ = _fake_factory([BackendResponse("never json", 5, 5)])
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("s")
        return wf.agent("q", label="q", schema=SCHEMA, tier="quality", required=False)

    rep = run_workflow(run_fn=run, slug="sf")
    assert rep["final"] is None and rep["failed_count"] == 1


def test_token_budget_blocks(monkeypatch):
    factory, _ = _fake_factory([BackendResponse("y" * 100, 1000, 1000)])
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("t")
        n = 0
        while wf.has_headroom(min_tokens=1):
            if wf.agent(f"q{n}", label=f"t{n}", tier="quality", required=False) is None:
                break
            n += 1
            if n > 50:
                break
        return n

    rep = run_workflow(run_fn=run, budget={"max_tokens": 5000}, slug="tok")
    assert rep["spend"]["tokens"] <= 6000
    assert rep["spend"]["calls"] <= 6
