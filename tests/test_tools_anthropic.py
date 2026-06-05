"""Native Anthropic tool-use loop — parity with the openai_http tool tests.

Hermetic: injects a fake ``anthropic`` module into sys.modules (no SDK install,
no network). Routes leaves to the anthropic_sdk backend via model="claude"
(the default config's anthropic-provider model).
"""
import sys
import types

from flow import ToolDefinition, register_tool
from flow.runtime import run_workflow
from flow.tools import to_anthropic_schema


# ---- fake anthropic SDK ------------------------------------------------------
class _Usage:
    def __init__(self, i=5, o=4):
        self.input_tokens, self.output_tokens = i, o


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Msg:
    def __init__(self, content, stop_reason, usage=None):
        self.content, self.stop_reason, self.usage = content, stop_reason, usage or _Usage()


class _Messages:
    def __init__(self, steps):
        self._steps, self.i = steps, 0

    def create(self, **kw):
        step = self._steps[min(self.i, len(self._steps) - 1)]
        self.i += 1
        return step


def _install_fake(monkeypatch, steps):
    mod = types.ModuleType("anthropic")

    class _Client:
        def __init__(self, **kw):
            self.messages = _Messages(steps)

    mod.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def _tool_use_step(name="get_price", args=None):
    return _Msg([_Block(type="tool_use", id="t1", name=name, input=args or {"symbol": "BTC"})],
                stop_reason="tool_use")


def _final_step(text="BTC is 42"):
    return _Msg([_Block(type="text", text=text)], stop_reason="end_turn")


def _mk_tool(name="get_price", approval=None, calls=None):
    def handler(symbol="BTC"):
        if calls is not None:
            calls.append(symbol)
        return {"price": 42, "symbol": symbol}

    register_tool(ToolDefinition(name=name, description="price",
                                 parameters={"type": "object", "properties": {"symbol": {"type": "string"}}},
                                 handler=handler, approval=approval))


# ---- tests -------------------------------------------------------------------
def test_anthropic_schema_shape():
    _mk_tool("an_rt_tool")
    sch = to_anthropic_schema(["an_rt_tool"])
    assert sch[0]["name"] == "an_rt_tool"
    assert "input_schema" in sch[0] and "type" not in sch[0]  # NOT the openai {type:function} shape


def test_anthropic_tool_loop_executes(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    calls = []
    _mk_tool("get_price", calls=calls)
    _install_fake(monkeypatch, [_tool_use_step(), _final_step()])

    def run(wf, args):
        wf.phase("p")
        return wf.agent("price of BTC?", label="q", tools=["get_price"], model="claude")

    rep = run_workflow(run_fn=run, slug="an-tl")
    assert rep["status"] == "completed" and rep["final"] == "BTC is 42"
    assert calls == ["BTC"]  # handler ran exactly once


def test_anthropic_tool_loop_caps(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    _mk_tool("get_price")
    _install_fake(monkeypatch, [_tool_use_step()])  # always asks for the tool → never settles

    def run(wf, args):
        wf.phase("p")
        return wf.agent("loop", label="q", tools=["get_price"], model="claude", required=False)

    rep = run_workflow(run_fn=run, slug="an-cap")
    assert rep["final"] is None and rep["failed_count"] == 1  # capped, not infinite


def test_anthropic_tool_approval_denied(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    _mk_tool("get_price", approval=lambda name, **kw: False)
    _install_fake(monkeypatch, [_tool_use_step(), _final_step()])

    def run(wf, args):
        wf.phase("p")
        return wf.agent("price?", label="q", tools=["get_price"], model="claude", required=False)

    rep = run_workflow(run_fn=run, slug="an-deny")
    assert rep["final"] is None and rep["failed_count"] == 1
    errs = " ".join(f.get("error", "") for f in rep["failed"])
    assert "tool_blocked" in errs


def test_anthropic_unknown_tool_is_error_not_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    _mk_tool("get_price")
    # model asks for a tool that isn't granted/registered, then settles
    _install_fake(monkeypatch, [_tool_use_step(name="ghost_tool"), _final_step("recovered")])

    def run(wf, args):
        wf.phase("p")
        return wf.agent("go", label="q", tools=["get_price"], model="claude", required=False)

    rep = run_workflow(run_fn=run, slug="an-ghost")
    assert rep["status"] == "completed" and rep["final"] == "recovered"  # tool_result is_error, model recovers
