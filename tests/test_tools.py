"""Phase 2B: first-class tools + grants + approval."""
from flow import register_tool, ToolDefinition
from flow.backends.openai_http import OpenAIHTTPBackend
from flow.runtime import run_workflow
from flow.tools import get_tool, to_openai_schema


def _mk_tool(name="get_price", approval=None, calls=None):
    def handler(symbol="BTC"):
        if calls is not None:
            calls.append(symbol)
        return {"price": 42, "symbol": symbol}
    register_tool(ToolDefinition(name=name, description="price",
                                 parameters={"type": "object", "properties": {"symbol": {"type": "string"}}},
                                 handler=handler, approval=approval))


def test_registry_and_schema():
    _mk_tool("rt_tool")
    assert get_tool("rt_tool") is not None
    sch = to_openai_schema(["rt_tool"])
    assert sch[0]["type"] == "function" and sch[0]["function"]["name"] == "rt_tool"


def _post_factory(steps):
    state = {"i": 0}

    def fake(url, body, headers, timeout):
        step = steps[min(state["i"], len(steps) - 1)]
        state["i"] += 1
        return step

    fake.state = state
    return fake


_TOOLCALL = {"choices": [{"message": {"role": "assistant", "tool_calls": [
    {"id": "c1", "function": {"name": "get_price", "arguments": '{"symbol":"BTC"}'}}]}}], "usage": {}}
_FINAL = {"choices": [{"message": {"content": "BTC is 42"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 4}}


def test_tool_loop_executes(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    calls = []
    _mk_tool("get_price", calls=calls)
    monkeypatch.setattr(OpenAIHTTPBackend, "_post", staticmethod(_post_factory([_TOOLCALL, _FINAL])))

    def run(wf, args):
        wf.phase("p")
        return wf.agent("price of BTC?", label="q", tools=["get_price"], tier="quality")

    rep = run_workflow(run_fn=run, slug="tl")
    assert rep["status"] == "completed" and rep["final"] == "BTC is 42"
    assert calls == ["BTC"]  # handler ran exactly once


def test_tool_loop_caps_iterations(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    _mk_tool("get_price")
    monkeypatch.setattr(OpenAIHTTPBackend, "_post", staticmethod(_post_factory([_TOOLCALL])))  # always tool_calls

    def run(wf, args):
        wf.phase("p")
        return wf.agent("loop", label="q", tools=["get_price"], tier="quality", required=False)

    rep = run_workflow(run_fn=run, slug="cap")
    assert rep["final"] is None and rep["failed_count"] == 1  # capped -> failed, not infinite


def test_tool_approval_denied(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    _mk_tool("get_price", approval=lambda name, **kw: False)  # deny
    monkeypatch.setattr(OpenAIHTTPBackend, "_post", staticmethod(_post_factory([_TOOLCALL, _FINAL])))

    def run(wf, args):
        wf.phase("p")
        return wf.agent("price?", label="q", tools=["get_price"], tier="quality", required=False)

    rep = run_workflow(run_fn=run, slug="deny")
    assert rep["final"] is None and rep["failed_count"] == 1
    errs = " ".join(f.get("error", "") for f in rep["failed"])
    assert "tool_blocked" in errs


def test_no_tools_omits_tools_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    seen = {}

    def fake(url, body, headers, timeout):
        seen["has_tools"] = "tools" in body
        return _FINAL

    monkeypatch.setattr(OpenAIHTTPBackend, "_post", staticmethod(fake))

    def run(wf, args):
        wf.phase("p")
        return wf.agent("hi", label="q", tier="quality")  # no tools

    run_workflow(run_fn=run, slug="nt")
    assert seen["has_tools"] is False  # backward compat: no tools key in body
