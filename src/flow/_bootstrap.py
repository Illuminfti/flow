"""init + self-test — zero-friction setup and proof-of-life."""
from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG = {
    "engine": {"executor": "thread", "budget": {"max_calls": 1000}},
    "leaf": {"allow_light_models": False},
    "providers": {
        "openai": {"kind": "openai_http", "base_url": "https://api.openai.com/v1", "auth_env": "OPENAI_API_KEY"},
        "anthropic": {"kind": "anthropic_sdk", "auth_env": "ANTHROPIC_API_KEY"},
        "deepseek": {"kind": "openai_http", "base_url": "https://api.deepseek.com", "auth_env": "DEEPSEEK_API_KEY"},
        "ollama": {"kind": "openai_http", "base_url": "${OLLAMA_HOST:-http://localhost:11434}/v1", "auth_env": None},
        # Subscriptions / OAuth routes. See docs/subscriptions.md
        "codex": {"kind": "codex", "auth_file": "~/.codex/auth.json",
                  "auth_field": "tokens.access_token", "account_field": "tokens.account_id"},
        "claude-cli": {"kind": "shell_cmd", "cmd_template": ["claude", "-p", "{prompt}"]},
    },
    "models": {
        "gpt-4o": {"provider": "openai", "id": "gpt-4o", "in": 2.5, "out": 10.0, "free": False,
                   "caps": ["reasoning", "tool_call", "structured", "vision"]},
        "claude-opus": {"provider": "anthropic", "id": "claude-opus-4-6", "in": 5.0, "out": 25.0, "free": False,
                        "caps": ["reasoning", "tool_call", "structured", "vision"]},
        "deepseek-v3": {"provider": "deepseek", "id": "deepseek-chat", "in": 0.27, "out": 1.10, "free": False,
                        "caps": ["reasoning", "tool_call", "structured"]},
        "llama3-local": {"provider": "ollama", "id": "llama3", "in": 0.0, "out": 0.0, "free": True,
                         "caps": ["tool_call"]},
        "gpt55-sub": {"provider": "codex", "id": "gpt-5.5", "in": 0.0, "out": 0.0, "free": True,
                      "caps": ["reasoning", "tool_call", "structured"]},
    },
    "tiers": {"quality": ["gpt-4o", "claude-opus"], "cheap": ["deepseek-v3", "gpt-4o"],
              "free": ["gpt55-sub", "llama3-local"], "local": []},
    "defaults": {"tier": "quality"},
}


def _yaml_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("yaml") is not None


def config_path() -> Path:
    if os.environ.get("FLOW_CONFIG"):
        return Path(os.environ["FLOW_CONFIG"])
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    # Write JSON by default (readable with zero deps); YAML only if PyYAML is
    # present, so the default install's `init`→`self-test` path always works.
    ext = "yaml" if _yaml_available() else "json"
    return Path(xdg) / "flow" / f"config.{ext}"


def cmd_init(force: bool = False) -> int:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not force:
        print(f"config already exists: {p} (use --force to overwrite)")
    else:
        if p.suffix == ".yaml":
            import yaml
            body = ("# flow config — https://github.com/Illuminfti/flow\n"
                    "# Add any model with zero code edits. Subscriptions: docs/subscriptions.md\n"
                    + yaml.safe_dump(_CONFIG, sort_keys=False))
        else:
            body = json.dumps(_CONFIG, indent=2)
        p.write_text(body, encoding="utf-8")
        print(f"wrote {p}")
    keys = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY") if os.environ.get(k)]
    print(f"detected API keys in env: {keys or 'none — export at least one, or run a local model via ollama'}")
    print("next: flow self-test --offline")
    return 0


def cmd_self_test(online: bool = False, as_json: bool = False) -> int:
    result = {"offline": {}, "online": {}}
    # offline: imports + config parse + local DAG, zero network
    try:
        import flow  # noqa
        from . import config
        from .paths import data_root
        cfg = config.get(reload=True)
        assert data_root().exists()
        from .runtime import run_workflow

        def _sq(x):
            return x * x

        def run(wf, args):
            wf.phase("selftest")
            return wf.parallel([(lambda i=i: wf.local(_sq, i, label=f"s{i}")) for i in range(4)])

        rep = run_workflow(run_fn=run, slug="selftest")
        ok = rep["status"] == "completed" and sorted(rep["final"]) == [0, 1, 4, 9]
        result["offline"] = {"ok": ok, "leaf_count": rep["leaf_count"],
                             "providers": list((cfg.get("providers") or {}).keys())}
    except Exception as exc:
        result["offline"] = {"ok": False, "error": str(exc)}

    if online and result["offline"].get("ok"):
        try:
            from .runtime import run_workflow

            def run(wf, args):
                wf.phase("ping")
                return wf.agent("Reply with exactly: OK", label="ping", tier=None)

            rep = run_workflow(run_fn=run, slug="selftest-online", budget={"max_calls": 2})
            result["online"] = {"ok": rep["status"] == "completed", "reply": str(rep.get("final"))[:80]}
        except Exception as exc:
            result["online"] = {"ok": False, "error": str(exc)}

    passed = result["offline"].get("ok") and (not online or result["online"].get("ok"))
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        o = result["offline"]
        print(f"offline: {'PASS' if o.get('ok') else 'FAIL'} "
              f"({o.get('leaf_count', '?')} leaves; providers={o.get('providers', [])})"
              + (f" err={o['error']}" if not o.get('ok') else ""))
        if online:
            n = result["online"]
            print(f"online:  {'PASS' if n.get('ok') else 'FAIL'} "
                  + (f"reply={n.get('reply')!r}" if n.get('ok') else f"err={n.get('error')}"))
    return 0 if passed else 1
