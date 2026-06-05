"""Structured-output enforcement with a bounded repair turn.

Hermes has no schema-forced JSON mode (recon-confirmed). The engine adds it as
a wrapper: inject the schema as a strict contract, parse the response, and on
parse/validation failure run exactly ONE repair turn. A second failure yields
status ``schema_failed`` surfaced to the script — never a silent bad value.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple

try:  # optional, only used if a leaf passes a pydantic model
    from pydantic import BaseModel  # type: ignore
    _HAS_PYDANTIC = True
except Exception:  # pragma: no cover
    BaseModel = None  # type: ignore
    _HAS_PYDANTIC = False

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def is_schema(schema: Any) -> bool:
    if schema is None:
        return False
    if isinstance(schema, dict):
        return True
    if _HAS_PYDANTIC and isinstance(schema, type) and issubclass(schema, BaseModel):
        return True
    return False


def _json_schema_of(schema: Any) -> dict:
    if isinstance(schema, dict):
        return schema
    if _HAS_PYDANTIC and isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    raise TypeError(f"unsupported schema type: {type(schema)}")


def instruction(schema: Any) -> str:
    js = _json_schema_of(schema)
    return (
        "\n\nYou MUST respond with ONLY a single JSON object (no prose, no code fence) "
        "that validates against this JSON Schema:\n"
        + json.dumps(js, ensure_ascii=False)
    )


def _extract_json(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    m = _FENCE.search(text)
    if m:
        return m.group(1).strip()
    # first balanced {...} or [...]
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    for j in range(start, len(text)):
        if text[j] == opener:
            depth += 1
        elif text[j] == closer:
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def _validate(value: Any, schema: Any) -> Tuple[bool, Optional[str]]:
    if _HAS_PYDANTIC and isinstance(schema, type) and issubclass(schema, BaseModel):
        try:
            schema.model_validate(value)
            return True, None
        except Exception as exc:  # pydantic ValidationError
            return False, str(exc)[:600]
    # dict JSON Schema: light structural check (required keys + type:object/array)
    js = schema if isinstance(schema, dict) else {}
    t = js.get("type")
    if t == "object" and not isinstance(value, dict):
        return False, f"expected object, got {type(value).__name__}"
    if t == "array" and not isinstance(value, list):
        return False, f"expected array, got {type(value).__name__}"
    if isinstance(value, dict):
        missing = [k for k in js.get("required", []) if k not in value]
        if missing:
            return False, f"missing required keys: {missing}"
    return True, None


def parse(text: str, schema: Any) -> Tuple[bool, Any, Optional[str]]:
    """Return (ok, value, error)."""
    raw = _extract_json(text)
    if raw is None:
        return False, None, "no JSON object found in response"
    try:
        value = json.loads(raw)
    except Exception as exc:
        return False, None, f"JSON parse error: {exc}"
    ok, err = _validate(value, schema)
    if not ok:
        return False, value, err
    return True, value, None


def repair_prompt(original_prompt: str, schema: Any, bad_text: str, error: str) -> str:
    return (
        f"{original_prompt}{instruction(schema)}\n\n"
        f"Your previous response FAILED schema validation with error: {error}\n"
        f"Previous response was:\n{bad_text[:1500]}\n\n"
        "Return ONLY the corrected JSON object. No explanation."
    )
