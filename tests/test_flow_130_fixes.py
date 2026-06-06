import time

import pytest

from flow import authoring
from flow.ids import leaf_id
from flow.runtime import ParallelError, run_workflow


def test_author_repairs_disallowed_import_once():
    calls = []
    bad = """```python
import os
def run(wf, args):
    return 1
```"""
    good = """```python
def run(wf, args):
    return {"ok": True}
```"""

    def chat(prompt):
        calls.append(prompt)
        return bad if len(calls) == 1 else good

    code = authoring.author("make a workflow", chat_fn=chat)

    assert len(calls) == 2
    assert "failed validation" in calls[1]
    assert "Do not import disallowed modules" in calls[1]
    assert "import os" not in code
    authoring.validate(code)


def test_author_repair_exhaustion_keeps_os_disallowed():
    def chat(_prompt):
        return """```python
import os
def run(wf, args):
    return 1
```"""

    with pytest.raises(authoring.AuthoringError) as exc:
        authoring.author("make a workflow", chat_fn=chat, repair_attempts=1)

    assert "disallowed import: os" in str(exc.value)
    with pytest.raises(authoring.AuthoringError, match="disallowed import: os"):
        authoring.validate("import os\ndef run(wf, args):\n    return 1")


def test_author_valid_first_response_does_not_repair():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return """```python
def run(wf, args):
    return 1
```"""

    authoring.author("make a workflow", chat_fn=chat)
    assert len(calls) == 1


def _boom_after(delay):
    time.sleep(delay)
    raise RuntimeError(f"boom-{delay}")


def test_parallel_fail_fast_uses_completion_order(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def run(wf, args):
        wf.phase("p")
        return wf.parallel([
            lambda: _boom_after(0.15),
            lambda: _boom_after(0.01),
        ], mode="fail_fast")

    rep = run_workflow(run_fn=run, slug="ff-order", max_workers=2)
    assert rep["status"] == "failed"
    assert "item 1 failed" in rep["error"]


def test_pipeline_accepts_stage_arities(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def one(cur):
        return cur + 1

    def two(cur, item):
        return cur + item

    def three(cur, item, idx):
        return cur + item + idx

    def varargs(*args):
        assert len(args) == 3
        cur, item, idx = args
        return cur + item + idx

    def run(wf, args):
        return wf.pipeline([1, 2], one, two, three, varargs)

    rep = run_workflow(run_fn=run, slug="pipe-arity")
    assert rep["status"] == "completed"
    assert rep["final"] == [5, 11]


def test_pipeline_invalid_arity_reports_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))

    def zero():
        return 1

    def run(wf, args):
        return wf.pipeline([1], zero, mode="fail_fast")

    rep = run_workflow(run_fn=run, slug="pipe-arity-bad")
    assert rep["status"] == "failed"
    assert "must accept at least one positional argument" in rep["error"]


def test_leaf_id_optional_fields_preserve_existing_and_split_new_identity():
    base = dict(run_script="s", phase="p", label="l", prompt="x", model="m", backend="b", schema="")
    old_shape = leaf_id(**base)
    explicit_empty = leaf_id(**base, provider="", toolsets="", tools="", max_tokens="", local="")
    assert old_shape == explicit_empty
    assert old_shape != leaf_id(**base, max_tokens="100")
    assert old_shape != leaf_id(**base, provider="openai")
    assert old_shape != leaf_id(**base, toolsets="web")
    assert old_shape != leaf_id(**base, tools="abc")


def test_local_schema_participates_in_resume_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    calls = {"n": 0}

    def value():
        calls["n"] += 1
        return {"a": "x", "b": "y"}

    s1 = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    s2 = {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}}

    def run(wf, args):
        return [wf.local(value, label="same", schema=s1), wf.local(value, label="same", schema=s2)]

    rep = run_workflow(run_fn=run, slug="local-schema", script_id="fixed")
    assert rep["status"] == "completed"
    assert calls["n"] == 2
