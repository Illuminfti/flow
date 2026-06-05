"""Token resolution for OAuth / subscription providers (offline)."""
import json

from flow import config
from flow.backends import build_backend
from flow.backends.codex import CodexBackend
from flow.leaves import LeafRequest
from flow.router import choose


def test_dig_nested_and_indexed():
    d = {"tokens": {"access_token": "T"}, "pool": {"x": [{"access_token": "P"}]}}
    assert config._dig(d, "tokens.access_token") == "T"
    assert config._dig(d, "pool.x[0].access_token") == "P"
    assert config._dig(d, "tokens.missing") is None


def test_resolve_token_from_env(monkeypatch):
    monkeypatch.setenv("MYKEY", "sk-env")
    assert config.resolve_token({"auth_env": "MYKEY"}) == "sk-env"


def test_resolve_token_from_file(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"tokens": {"access_token": "oauth-abc"}}))
    assert config.resolve_token({"auth_file": str(p), "auth_field": "tokens.access_token"}) == "oauth-abc"


def test_resolve_token_from_cmd():
    assert config.resolve_token({"auth_cmd": "printf oauth-cmd"}) == "oauth-cmd"


def test_resolve_token_priority(monkeypatch, tmp_path):
    # env wins over file
    monkeypatch.setenv("WINS", "from-env")
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"t": "from-file"}))
    assert config.resolve_token({"auth_env": "WINS", "auth_file": str(p), "auth_field": "t"}) == "from-env"


def test_codex_backend_built_from_config(monkeypatch, tmp_path):
    auth = tmp_path / "codex.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "tok", "account_id": "acct"}}))
    cfg = {
        "providers": {"codex": {"kind": "codex", "auth_file": str(auth),
                                "auth_field": "tokens.access_token", "account_field": "tokens.account_id"}},
        "models": {"g": {"provider": "codex", "id": "gpt-5.5", "in": 0, "out": 0, "free": True,
                         "caps": ["reasoning", "tool_call", "structured"]}},
        "tiers": {"quality": ["g"], "cheap": ["g"], "free": ["g"], "local": []},
        "leaf": {"allow_light_models": False, "denylist": ""},
        "defaults": {"tier": "quality", "backend": "codex"},
    }
    config.set_config(cfg)
    route = choose(tier="quality")
    assert route.backend == "codex"
    b = build_backend(LeafRequest(leaf_id="x", label="x", phase="p", prompt="hi", route=route))
    assert isinstance(b, CodexBackend)
    assert b.token == "tok" and b.account_id == "acct" and b.model == "gpt-5.5"


def test_openai_http_custom_headers(monkeypatch, tmp_path):
    auth = tmp_path / "t.json"
    auth.write_text(json.dumps({"k": "oauthtok"}))
    cfg = {
        "providers": {"p": {"kind": "openai_http", "base_url": "http://x/v1",
                            "auth_file": str(auth), "auth_field": "k",
                            "headers": {"X-Title": "flow"}}},
        "models": {"m": {"provider": "p", "id": "m1", "in": 0, "out": 0, "free": True,
                         "caps": ["structured"]}},
        "tiers": {"quality": ["m"], "cheap": ["m"], "free": ["m"], "local": []},
        "leaf": {"denylist": ""}, "defaults": {"tier": "quality", "backend": "openai_http"},
    }
    config.set_config(cfg)
    route = choose(tier="quality")
    b = build_backend(LeafRequest(leaf_id="x", label="x", phase="p", prompt="hi", route=route))
    assert b.api_key == "oauthtok" and b.extra_headers.get("X-Title") == "flow"
