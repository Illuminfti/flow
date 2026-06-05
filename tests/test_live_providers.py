"""Phase 3C: credential-gated LIVE provider tests. Skipped when tokens absent.

Run explicitly:  pytest -m live
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def test_openai_compatible_live():
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("no OPENAI_API_KEY/OPENROUTER_API_KEY")
    from flow.backends.openai_http import OpenAIHTTPBackend
    from flow.backends.base import BackendError
    base = "https://openrouter.ai/api/v1" if os.environ.get("OPENROUTER_API_KEY") else "https://api.openai.com/v1"
    model = os.environ.get("FLOW_LIVE_MODEL", "gpt-4o")
    try:
        r = OpenAIHTTPBackend(base_url=base, api_key=key, model=model, provider="live")("Reply with: pong")
    except BackendError as e:
        if getattr(e, "status", None) in (402, 429):
            pytest.skip(f"provider credit/rate-limit ({e.status}) — auth reached the server, no quota")
        raise
    assert isinstance(r.text, str) and r.text
    assert r.tokens_estimated is False  # real usage reported by a real server


def test_codex_oauth_live():
    auth = Path("~/.codex/auth.json").expanduser()
    if not auth.exists():
        pytest.skip("no ~/.codex/auth.json")
    from flow.backends.codex import CodexBackend
    from flow.config import _dig, resolve_token
    import json
    tok = resolve_token({"auth_file": str(auth), "auth_field": "tokens.access_token"})
    acct = _dig(json.loads(auth.read_text()), "tokens.account_id") or ""
    if not tok:
        pytest.skip("no codex access_token")
    r = CodexBackend(token=tok, model="gpt-5.5", account_id=acct)("Reply with exactly: pong", timeout=120)
    assert r.text.strip()
