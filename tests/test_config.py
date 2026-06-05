import json

from flow import config


def test_env_interpolation(monkeypatch, tmp_path):
    monkeypatch.setenv("MYHOST", "http://h:1234")
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"providers": {"o": {"kind": "openai_http", "base_url": "${MYHOST}/v1"}}}))
    monkeypatch.setenv("FLOW_CONFIG", str(p))
    cfg = config.get(reload=True)
    assert cfg["providers"]["o"]["base_url"] == "http://h:1234/v1"


def test_interpolation_default(monkeypatch, tmp_path):
    monkeypatch.delenv("NOPE", raising=False)
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"providers": {"o": {"base_url": "${NOPE:-http://fallback/v1}"}}}))
    monkeypatch.setenv("FLOW_CONFIG", str(p))
    cfg = config.get(reload=True)
    assert cfg["providers"]["o"]["base_url"] == "http://fallback/v1"


def test_defaults_merged(monkeypatch, tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"models": {}}))
    monkeypatch.setenv("FLOW_CONFIG", str(p))
    cfg = config.get(reload=True)
    assert cfg["leaf"]["allow_light_models"] is False
    assert "denylist" in cfg["leaf"]
    assert cfg["defaults"]["tier"] == "quality"


def test_zero_config(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOW_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.flow.yaml
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nope"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = config.get(reload=True)
    assert "openai" in cfg["providers"]
    assert cfg["tiers"]["quality"]  # a model was synthesised
