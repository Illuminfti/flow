"""Proof tests for the Phase-1 correctness fixes (F1-F7)."""
import sys
import threading
import time
import types

import pytest

from flow import authoring, config, leaves
from flow.backends.base import BackendResponse
from flow.journal import Journal
from flow.runtime import run_workflow


# -- F1: cached hit respects deadline --------------------------------------
def test_cached_hit_respects_deadline():
    def once(i):
        return i

    def run(wf, args):
        wf.phase("p")
        return wf.local(once, 7, label="c")

    rid = "f1-deadline"
    r1 = run_workflow(run_fn=run, run_id=rid, script_id="f1", slug="f1")
    assert r1["final"] == 7
    # Re-run with an already-passed deadline; the cached leaf must fail, not serve stale.
    r2 = run_workflow(run_fn=run, run_id=rid, script_id="f1", slug="f1",
                      budget={"deadline_seconds": -1})
    assert r2["status"] == "failed"
    assert r2["final"] is None


# -- F2: anthropic flags estimated tokens ----------------------------------
def _fake_anthropic(in_tok, out_tok):
    mod = types.ModuleType("anthropic")

    class _Usage:
        input_tokens = in_tok
        output_tokens = out_tok

    class _Block:
        type = "text"
        text = "hi"

    class _Msg:
        content = [_Block()]
        usage = _Usage()

    class _Client:
        def __init__(self, **kw): pass
        @property
        def messages(self):
            class M:
                def create(self, **kw): return _Msg()
            return M()

    mod.Anthropic = _Client
    return mod


def test_anthropic_flags_estimated(monkeypatch):
    from flow.backends.anthropic_sdk import AnthropicBackend
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic(None, None))
    r = AnthropicBackend(api_key="k", model="m")("hi")
    assert r.tokens_estimated is True
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic(11, 5))
    r2 = AnthropicBackend(api_key="k", model="m")("hi")
    assert r2.tokens_estimated is False and r2.input_tokens == 11


# -- F4: journal atomic write — concurrent same id + tmp cleanup -----------
def test_atomic_write_concurrent_same_id(tmp_path):
    j = Journal(tmp_path / "run").open()
    errs = []

    def w():
        try:
            j.write_leaf_result("same", {"k": "v"})
        except Exception as e:
            errs.append(e)

    ts = [threading.Thread(target=w) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    j.close()
    assert not errs
    assert j.read_leaf_result("same") == {"k": "v"}
    assert not list((tmp_path / "run" / "leaves").glob("*.tmp"))


def test_atomic_write_cleans_tmp_on_error(tmp_path):
    j = Journal(tmp_path / "run").open()
    circular = {}
    circular["self"] = circular  # json.dump raises ValueError (circular ref)
    with pytest.raises(Exception):
        j.write_leaf_result("bad", circular)
    j.close()
    assert not list((tmp_path / "run" / "leaves").glob("*.tmp"))


# -- F5: schema repair respects budget (2x reserve) ------------------------
def test_schema_repair_respects_budget(monkeypatch):
    SCHEMA = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}

    def factory(req):
        def backend(prompt, *, timeout=None):
            return BackendResponse("not json", 1000, 1000)
        return backend

    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("s")
        # one call ~ est 1+400 reserved *2 for schema = 802; cap at 500 blocks it
        return wf.agent("q", label="q", schema=SCHEMA, tier="quality", required=False)

    rep = run_workflow(run_fn=run, budget={"max_tokens": 500}, slug="f5")
    assert rep["final"] is None
    assert rep["failed_count"] == 1


# -- F6: config interpolation is linear-time (no ReDoS) --------------------
def test_interpolation_no_catastrophic_backtracking():
    # `[^}]*` (not `.*?`) means the default branch cannot backtrack into the
    # closing brace — no exponential blowup. A pathological all-"${" string
    # finishes fast; an exponential regex would hang for many seconds.
    payload = "${VAR:-" * 2000
    t0 = time.time()
    out = config._interpolate(payload)
    assert time.time() - t0 < 2.0
    assert out == payload  # no closing brace -> unchanged


# -- F7: authoring AST allowlist hardening ---------------------------------
def test_rejects_dunder_import():
    with pytest.raises(authoring.AuthoringError):
        authoring.validate("def run(wf, args):\n    return __import__('os').system('echo hi')")


def test_rejects_module_level_code():
    with pytest.raises(authoring.AuthoringError):
        authoring.validate("import os\nos.system('x')\ndef run(wf, args):\n    return 1")


def test_rejects_eval():
    with pytest.raises(authoring.AuthoringError):
        authoring.validate("def run(wf, args):\n    return eval('1+1')")


def test_accepts_valid_script():
    authoring.validate(
        "FINDING = {'type': 'object'}\n"
        "def run(wf, args):\n"
        "    return wf.parallel([(lambda i=i: wf.agent(f'q{i}', label='a')) for i in range(2)])"
    )
