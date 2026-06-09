"""Baseline run-store helpers for the WS-H performance harness.

Loads persisted run-report.json and manifest.json from a local flow-runs store
(FLOW_BASELINE_RUNS env or ~/.hermes/flow-runs/runs). Never copies chunk/context
content into the repo — loads at runtime only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pytest

BASELINE_ROOT = Path(
    os.environ.get("FLOW_BASELINE_RUNS", "~/.hermes/flow-runs/runs")
).expanduser()


def load_baseline(run_id_glob: str) -> Optional[dict]:
    """Find the run directory matching *run_id_glob* and return a dict with
    ``run_report`` and ``manifest`` keys (both parsed). Returns None when the
    store is absent or no matching directory is found."""
    if not BASELINE_ROOT.exists():
        return None
    matches = sorted(BASELINE_ROOT.glob(run_id_glob))
    if not matches:
        return None
    run_dir = matches[0]
    result: dict = {"run_dir": run_dir}
    rr = run_dir / "run-report.json"
    mf = run_dir / "manifest.json"
    if rr.exists():
        result["run_report"] = json.loads(rr.read_text(encoding="utf-8"))
    else:
        result["run_report"] = None
    if mf.exists():
        result["manifest"] = json.loads(mf.read_text(encoding="utf-8"))
    else:
        result["manifest"] = None
    return result


def require_baseline(run_id_glob: str) -> dict:
    """Return the baseline dict or skip the test if the store is absent."""
    bl = load_baseline(run_id_glob)
    if bl is None:
        pytest.skip(f"baseline store not found: {BASELINE_ROOT} / {run_id_glob}")
    return bl
