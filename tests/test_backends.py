from flowleaf.backends import build_backend
from flowleaf.backends.local import LocalBackend
from flowleaf.backends.openai_http import OpenAIHTTPBackend
from flowleaf.backends.shell_cmd import ShellCmdBackend
from flowleaf.leaves import LeafRequest
from flowleaf.router import choose


def test_local_backend_preserves_native():
    b = LocalBackend(lambda: {"x": 1})
    r = b("ignored")
    assert r.native == {"x": 1} and r.input_tokens == 0


def test_openai_http_parses(monkeypatch):
    def fake_post(url, body, headers, timeout):
        assert url.endswith("/chat/completions")
        return {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    monkeypatch.setattr(OpenAIHTTPBackend, "_post", staticmethod(fake_post))
    b = OpenAIHTTPBackend(base_url="http://x/v1", api_key="k", model="m", provider="p", pricing=(1.0, 2.0))
    r = b("hi")
    assert r.text == "hello" and r.input_tokens == 7 and r.output_tokens == 3
    assert abs(r.usd - ((7 / 1e6) * 1.0 + (3 / 1e6) * 2.0)) < 1e-12


def test_shell_cmd_echo():
    b = ShellCmdBackend(cmd_template=["echo", "{prompt}"], model="m", provider="sh")
    r = b("ping pong")
    assert r.text == "ping pong"


def test_factory_resolves_kinds():
    route = choose(tier="quality")  # openai_http via 'test' provider
    req = LeafRequest(leaf_id="x", label="x", phase="p", prompt="hi", route=route)
    b = build_backend(req)
    assert isinstance(b, OpenAIHTTPBackend)
