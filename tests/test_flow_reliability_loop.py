from flow import leaves
from flow.backends.base import BackendResponse
from flow.runtime import run_workflow

SCHEMA = {"type": "object", "required": ["title", "severity"],
          "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}


def _fake_factory(responses):
    state = {"i": 0}

    def factory(req):
        def backend(prompt, *, timeout=None):
            r = responses[min(state["i"], len(responses) - 1)]
            state["i"] += 1
            return r
        return backend
    return factory, state


def test_optional_schema_failure_is_warning_not_generic_run_failure(monkeypatch):
    factory, _ = _fake_factory([BackendResponse("not json", 5, 5), BackendResponse("still not json", 5, 5)])
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("parse")
        optional = wf.agent("optional lens", label="optional-lens", schema=SCHEMA, required=False)
        return {"optional": optional, "usable": "kept"}

    rep = run_workflow(run_fn=run, slug="optional-warning")
    assert rep["status"] == "completed_with_warnings"
    assert rep["final"] == {"optional": None, "usable": "kept"}
    assert rep["failed_count"] == 1
    assert rep["required_failed_count"] == 0
    assert rep["warnings"][0]["label"] == "optional-lens"
    assert rep["warnings"][0]["error_kind"] == "parse_error"


def test_schema_poison_leaf_repairs_until_valid(monkeypatch):
    factory, state = _fake_factory([
        BackendResponse("not json", 5, 5),
        BackendResponse('{"title":"missing severity"}', 7, 7),
        BackendResponse('{"title":"fixed","severity":"high"}', 11, 13),
    ])
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("repair")
        return wf.agent("repairable lens", label="repairable-lens", schema=SCHEMA)

    rep = run_workflow(run_fn=run, slug="repair-poison")
    assert rep["status"] == "completed"
    assert rep["final"] == {"title": "fixed", "severity": "high"}
    assert rep["failed_count"] == 0
    assert rep["warning_count"] == 0
    assert rep["self_healed_count"] == 1
    assert rep["self_healed"][0]["label"] == "repairable-lens"
    assert rep["self_healed"][0]["repair_attempts"] == 2
    assert state["i"] == 3


def test_schema_poison_leaf_uses_last_repair_error_after_exhaustion(monkeypatch):
    factory, state = _fake_factory([
        BackendResponse("not json", 5, 5),
        BackendResponse('{"title":"still missing severity"}', 7, 7),
        BackendResponse('{"title":"still poisoned"}', 11, 13),
    ])
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("repair")
        return wf.agent("unrepairable lens", label="unrepairable-lens", schema=SCHEMA, required=False)

    rep = run_workflow(run_fn=run, slug="unrepairable-poison")
    assert rep["status"] == "completed_with_warnings"
    assert rep["final"] is None
    assert rep["warnings"][0]["label"] == "unrepairable-lens"
    assert rep["warnings"][0]["error_kind"] == "schema_error"
    assert rep["warnings"][0]["repair_attempts"] == 2
    assert rep["self_healed_count"] == 0
    assert rep["self_healed"] == []
    assert "severity" in rep["warnings"][0]["error"]
    assert state["i"] == 3


def test_required_schema_failure_stays_fatal_with_error_kind(monkeypatch):
    factory, _ = _fake_factory([BackendResponse("not json", 5, 5), BackendResponse("still not json", 5, 5)])
    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("parse")
        return wf.agent("required lens", label="required-lens", schema=SCHEMA)

    rep = run_workflow(run_fn=run, slug="required-fail")
    assert rep["status"] == "failed"
    assert rep["required_failed_count"] == 1
    assert rep["failed"][0]["label"] == "required-lens"
    assert rep["failed"][0]["error_kind"] == "parse_error"


def test_finalization_failure_after_completed_leaves_preserves_fallback():
    def run(wf, args):
        wf.phase("leaves")
        wf.local(lambda: "done", label="useful")
        raise RuntimeError("final synthesis budget exceeded")

    rep = run_workflow(run_fn=run, slug="final-fallback")
    assert rep["status"] == "completed_with_warnings"
    assert rep["leaf_count"] == 1
    assert rep["finalization_error"] == "final synthesis budget exceeded"
    assert rep["final"]["fallback"] == "finalization_failed_after_completed_leaves"
    assert rep["final"]["completed_leaf_count"] == 1


def test_oversized_leaf_text_is_persisted_and_preview_truncated():
    huge = "x" * 13000

    def run(wf, args):
        wf.phase("artifacts")
        return wf.local(lambda: huge, label="huge-output")

    rep = run_workflow(run_fn=run, slug="huge-artifact")
    assert rep["status"] == "completed"
    assert rep["artifacts"]
    ref = rep["artifacts"][0]
    assert ref["kind"] == "leaf_text"
    assert ref["char_count"] == len(huge)
    assert ref["preview_char_count"] == 12000
    leaf_path = rep["run_dir"] + "/leaves/" + next(__import__("os").walk(rep["run_dir"] + "/leaves"))[2][0]
    data = __import__("json").load(open(leaf_path))
    assert len(data["text"]) == 12000
    assert open(rep["run_dir"] + "/" + ref["path"]).read() == huge
