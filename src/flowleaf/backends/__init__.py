"""Backend registry + factory.

``build_backend(req)`` resolves a LeafRequest's route into a concrete Backend
using the active config. ``register_backend(kind, builder)`` lets downstream
users add custom backends (e.g. a Hermes in-process agent, a bespoke gateway)
without forking.
"""
from __future__ import annotations

from typing import Callable

from .. import config as _config
from .base import Backend, BackendError, BackendResponse
from .anthropic_sdk import AnthropicBackend
from .local import LocalBackend
from .openai_http import OpenAIHTTPBackend
from .shell_cmd import ShellCmdBackend

# builder signature: (req, cfg, provider_cfg) -> Backend
_REGISTRY: dict[str, Callable] = {}


def register_backend(kind: str, builder: Callable) -> None:
    _REGISTRY[kind] = builder


def _build_openai_http(req, cfg, prov) -> Backend:
    return OpenAIHTTPBackend(
        base_url=prov.get("base_url", "https://api.openai.com/v1"),
        api_key=_config.api_key_for(prov),
        model=req.route.model,
        provider=req.route.provider,
        max_tokens=req.max_tokens,
        pricing=req.route.pricing,
    )


def _build_anthropic(req, cfg, prov) -> Backend:
    return AnthropicBackend(
        api_key=_config.api_key_for(prov),
        model=req.route.model,
        provider=req.route.provider,
        max_tokens=req.max_tokens or 4096,
        pricing=req.route.pricing,
    )


def _build_shell_cmd(req, cfg, prov) -> Backend:
    template = prov.get("cmd_template")
    if not template:
        raise BackendError(f"provider {req.route.provider!r} (shell_cmd) needs a cmd_template")
    leaf = cfg.get("leaf") or {}
    return ShellCmdBackend(
        cmd_template=list(template),
        model=req.route.model,
        provider=req.route.provider,
        toolsets=req.toolsets or "",
        pricing=req.route.pricing,
        noise_patterns=list(leaf.get("noise_patterns") or []),
        extra_args=list(prov.get("extra_args") or []),
    )


def _build_local(req, cfg, prov) -> Backend:
    return LocalBackend(req.local_fn, req.local_args, req.local_kwargs)


register_backend("openai_http", _build_openai_http)
register_backend("anthropic_sdk", _build_anthropic)
register_backend("shell_cmd", _build_shell_cmd)
register_backend("local", _build_local)


def build_backend(req) -> Backend:
    kind = req.route.backend
    builder = _REGISTRY.get(kind)
    if builder is None:
        raise BackendError(f"unknown backend kind {kind!r}; registered: {sorted(_REGISTRY)}")
    cfg = _config.get()
    prov = {} if kind == "local" else _config.provider_of(cfg, req.route.provider)
    return builder(req, cfg, prov)


__all__ = ["Backend", "BackendError", "BackendResponse", "build_backend", "register_backend"]
