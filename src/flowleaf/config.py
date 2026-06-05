"""Configuration — makes flowleaf work with ANY models on ANY box, zero code edits.

Load precedence:
  1. $FLOWLEAF_CONFIG
  2. ./.flowleaf.yaml
  3. $XDG_CONFIG_HOME/flowleaf/config.yaml  (default ~/.config/flowleaf/config.yaml)
  4. zero-config: if OPENAI_API_KEY is set, synthesise a one-model "quality" setup.

``${VAR}`` / ``${VAR:-default}`` interpolation (one level) lets you reference env
vars (API keys, hosts) without hardcoding secrets in the file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class ConfigError(RuntimeError):
    pass


# Minimal, vendor-neutral defaults. The light-model denylist is OPT-IN-to-disable
# (allow_light_models) — it is a sensible default, not anyone's house policy.
_DEFAULTS: dict[str, Any] = {
    "engine": {
        "executor": "thread",
        "max_workers": None,
        "orch_workers": None,
        "data_dir": None,
        "budget": {"max_tokens": None, "max_usd": None, "max_calls": 1000, "deadline_seconds": None},
    },
    "leaf": {
        "allow_light_models": False,
        "denylist": r"(?:^|[-_/])(mini|flash|haiku|nano|lite)(?:$|[-_/0-9.])",
        "noise_patterns": [],
        "ignore_user_config": True,
    },
    "providers": {},
    "models": {},
    "tiers": {"quality": [], "cheap": [], "free": [], "local": []},
    "defaults": {"tier": "quality", "backend": "openai_http"},
}


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else "")
        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _candidate_paths() -> list[Path]:
    paths = []
    if os.environ.get("FLOWLEAF_CONFIG"):
        paths.append(Path(os.environ["FLOWLEAF_CONFIG"]))
    paths.append(Path.cwd() / ".flowleaf.yaml")
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    paths.append(Path(xdg) / "flowleaf" / "config.yaml")
    return paths


def _zero_config() -> Optional[dict]:
    """If a single OpenAI-compatible key is present, synthesise a usable setup.
    Never auto-selects a light model."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    model = os.environ.get("FLOWLEAF_DEFAULT_MODEL", "gpt-4o")
    return {
        "providers": {"openai": {"kind": "openai_http", "base_url": "https://api.openai.com/v1", "auth_env": "OPENAI_API_KEY"}},
        "models": {model: {"provider": "openai", "id": model, "in": 2.5, "out": 10.0, "free": False,
                           "caps": ["reasoning", "tool_call", "structured"]}},
        "tiers": {"quality": [model], "cheap": [model], "free": [], "local": []},
    }


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # optional extra
    except ImportError:
        raise ConfigError(
            f"{path} is YAML but PyYAML is not installed. Run: pip install 'flowleaf[yaml]' "
            f"(or set FLOWLEAF_CONFIG to a .json file)."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ConfigError(f"failed to parse {path}: {exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping at top level")
    return data


def _load_json(path: Path) -> dict:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"failed to parse {path}: {exc}")


def load(path: Optional[str] = None) -> dict:
    """Load + merge config. Returns the fully-resolved (interpolated) dict."""
    chosen: Optional[Path] = None
    if path:
        chosen = Path(path)
        if not chosen.exists():
            raise ConfigError(f"config not found: {path}")
    else:
        for p in _candidate_paths():
            if p.exists():
                chosen = p
                break

    user: dict = {}
    if chosen is not None:
        user = _load_json(chosen) if chosen.suffix == ".json" else _load_yaml(chosen)
    else:
        zc = _zero_config()
        if zc is not None:
            user = zc

    merged = _deep_merge(_DEFAULTS, user)
    return _interpolate(merged)


# ---- singleton -----------------------------------------------------------
_CACHE: Optional[dict] = None


def get(reload: bool = False) -> dict:
    global _CACHE
    if _CACHE is None or reload:
        _CACHE = load()
    return _CACHE


def set_config(cfg: dict) -> None:
    """Override the active config (tests, embedding)."""
    global _CACHE
    _CACHE = cfg


def provider_of(cfg: dict, name: str) -> dict:
    p = (cfg.get("providers") or {}).get(name)
    if not p:
        raise ConfigError(f"unknown provider {name!r}; define it under providers: in your config")
    return p


def api_key_for(provider: dict) -> str:
    env = provider.get("auth_env")
    return os.environ.get(env, "") if env else ""
