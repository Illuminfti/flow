import json

import pytest

from flow import config

TEST_CONFIG = {
    "engine": {"executor": "thread", "budget": {"max_calls": 1000},
               "retry": {"max_attempts": 3, "base_ms": 1, "cap_ms": 5}},
    "leaf": {
        "allow_light_models": False,
        "denylist": r"(?:^|[-_/])(mini|flash|haiku|nano|lite)(?:$|[-_/0-9.])",
        "noise_patterns": [],
    },
    "providers": {
        "test": {"kind": "openai_http", "base_url": "http://localhost:0/v1", "auth_env": "TEST_KEY"},
        "sh": {"kind": "shell_cmd", "cmd_template": ["echo", "{prompt}"]},
        "anthropic": {"kind": "anthropic_sdk", "auth_env": "ANTHROPIC_API_KEY"},
    },
    "models": {
        "big": {"provider": "test", "id": "big-model", "in": 1.0, "out": 2.0, "free": False,
                "caps": ["reasoning", "tool_call", "structured", "vision"]},
        "cheapo": {"provider": "test", "id": "cheap-model", "in": 0.1, "out": 0.2, "free": False,
                   "caps": ["tool_call", "structured"]},
        "freebie": {"provider": "test", "id": "free-model", "in": 0.0, "out": 0.0, "free": True,
                    "caps": ["tool_call", "structured", "vision"]},
        "claude": {"provider": "anthropic", "id": "claude-test", "in": 3.0, "out": 15.0, "free": False,
                   "caps": ["reasoning", "tool_call", "structured"]},
    },
    "tiers": {"quality": ["big"], "cheap": ["cheapo", "big"], "free": ["freebie"], "local": []},
    "defaults": {"tier": "quality", "backend": "openai_http"},
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "data"))
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(TEST_CONFIG))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)
    yield
    config.set_config(None) if hasattr(config, "set_config") else None
