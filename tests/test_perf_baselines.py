"""WS-H performance harness + acceptance suite (plan §5.3/§7).

Hermetic tests run always; live tests require -m live, a local baseline store
(FLOW_BASELINE_RUNS), and the local-only ``tests/baselines/`` workload ports
(gitignored — they embed private workload prompts). Without those, live tests
skip. Live tests replay persisted real-world manifests through v2 under the
identical max_tokens ceiling the v1 run died under and assert completed status.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flow import config
from flow.backends.pricing import estimate_tokens
from flow.blocks import dedup_report
from flow.paths import run_dir_for
from flow.runtime import run_workflow
from tests._baseline_harness import BASELINE_ROOT, load_baseline, require_baseline

LIVE_CONFIG = Path(os.environ.get(
    "FLOW_LIVE_CONFIG", "~/.hermes/flow-config.json")).expanduser()
LIVE_DATA_DIR = Path("~/.local/share/flow-perf").expanduser()


@pytest.fixture()
def live_env(monkeypatch):
    """Re-point config + data dir at the real provisioned environment — the
    autouse `isolated` fixture otherwise routes live tests at a dead localhost
    provider and a throwaway tmp data dir."""
    if not LIVE_CONFIG.exists():
        pytest.skip(f"live config not found at {LIVE_CONFIG}")
    monkeypatch.setenv("FLOW_CONFIG", str(LIVE_CONFIG))
    monkeypatch.setenv("FLOW_DATA_DIR", str(LIVE_DATA_DIR))
    config.get(reload=True)
    yield
    config.get(reload=True)

# ---------------------------------------------------------------------------
# Shared echo config for hermetic (no-LLM) tests
# ---------------------------------------------------------------------------

ECHO_CFG = {
    "engine": {"executor": "thread"},
    "leaf": {"allow_light_models": True, "noise_patterns": []},
    "providers": {"sh": {"kind": "shell_cmd", "cmd_template": ["echo", "{prompt}"]}},
    "models": {
        "echo": {
            "provider": "sh", "id": "echo", "in": 0.0, "out": 0.0,
            "free": True, "caps": [],
        }
    },
    "tiers": {"quality": ["echo"], "local": []},
    "defaults": {"tier": "quality"},
}

_SHARED_BLOB = "\n".join(
    f"shared context line {i} " + "x" * 38 for i in range(200)
)  # ~8000 chars


def _echo_cfg(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)


# ---------------------------------------------------------------------------
# Deterministic v1 baseline pin (no LLM, reads persisted run-report.json)
# ---------------------------------------------------------------------------

def test_baseline_5382_recorded_failure():
    """Pins the deterministic v1 5382 baseline from disk — status=failed,
    breach message contains the exact token figures from the run."""
    bl = load_baseline("tool-20260607T230440-130250-5382")
    if bl is None or bl.get("run_report") is None:
        pytest.skip(f"baseline store not found at {BASELINE_ROOT}")
    rr = bl["run_report"]
    assert rr["status"] == "failed"
    err = rr.get("error") or ""
    assert "906313+161364 > 900000" in err, (
        f"expected breach figures not in error: {err!r}"
    )


# ---------------------------------------------------------------------------
# Live headline tests — require -m live + baseline store
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_loop_beats_v1_baseline_on_5382(live_env):
    """V2 port of the 5382 SOUL.md research workload completes under the same
    900k-token ceiling that killed v1, with >=70% fan-out dedup."""
    bl = require_baseline("tool-20260607T202625-892502-5382")
    args = bl["manifest"]["args"]
    budget = {"max_tokens": 900000, "max_calls": 80, "max_usd": 5}

    from tests.baselines import wf_5382_v2
    report = run_workflow(
        run_fn=wf_5382_v2.run,
        args=args,
        budget=budget,
        run_id="perf-5382-v2",
        script_id="wf_5382_v2",
        slug="perf",
    )
    assert report["status"] in ("completed", "completed_with_warnings"), (
        f"expected completed, got {report['status']}: {report.get('error')}"
    )
    final = report["final"]
    assert final.get("goal_met") is True, (
        f"loop did not meet goal: stop_reason={final.get('stop_reason')}"
    )
    assert report["spend"]["input_tokens"] <= 250_000, (
        f"input_tokens {report['spend']['input_tokens']} > 250k ceiling"
    )
    dr = dedup_report(run_dir_for("perf-5382-v2"))
    assert dr["dedup_rate"] >= 0.70, (
        f"dedup_rate {dr['dedup_rate']} < 0.70"
    )


@pytest.mark.live
def test_loop_beats_v1_baseline_on_f60c(live_env):
    """V2 port of the f60c TGCRM gap-analysis workload completes under the
    same 300k-token ceiling that killed v1."""
    bl = require_baseline("flow-20260608T224228-802539-f60c")
    args = bl["manifest"]["args"]
    budget = {"max_tokens": 300000, "max_usd": 5, "max_calls": 40}

    from tests.baselines import wf_f60c_v2
    report = run_workflow(
        run_fn=wf_f60c_v2.run,
        args=args,
        budget=budget,
        run_id="perf-f60c-v2",
        script_id="wf_f60c_v2",
        slug="perf",
    )
    assert report["status"] in ("completed", "completed_with_warnings"), (
        f"expected completed, got {report['status']}: {report.get('error')}"
    )


@pytest.mark.live
def test_cache_hit_rate_on_named_run(live_env):
    """Piggybacks the 5382 v2 run (or reuses persisted run dir) and asserts
    the measured dedup rate >= 0.70 from persisted per-leaf input hashes."""
    bl = require_baseline("tool-20260607T202625-892502-5382")
    run_dir = run_dir_for("perf-5382-v2")
    if not (run_dir / "journal.jsonl").exists():
        # Run it first.
        args = bl["manifest"]["args"]
        from tests.baselines import wf_5382_v2
        run_workflow(
            run_fn=wf_5382_v2.run,
            args=args,
            budget={"max_tokens": 900000, "max_calls": 80, "max_usd": 5},
            run_id="perf-5382-v2",
            script_id="wf_5382_v2",
            slug="perf",
        )
    dr = dedup_report(run_dir)
    assert dr["dedup_rate"] >= 0.70, (
        f"measured dedup_rate {dr['dedup_rate']} < 0.70 "
        f"(charged={dr['shared_tokens_charged']}, "
        f"would_be={dr['shared_tokens_would_be']})"
    )
    assert dr["leaves_with_input_hash"] > 0


# ---------------------------------------------------------------------------
# Hermetic structural dedup test — no LLM
# ---------------------------------------------------------------------------

def test_fanout_dedup_structural(tmp_path, monkeypatch):
    """One ~8k-char shared block, 8 sibling leaves — dedup_rate >= 0.70 and
    budget charged the block once (not 8 times)."""
    _echo_cfg(tmp_path, monkeypatch)

    blob = _SHARED_BLOB
    blob_tokens = estimate_tokens(blob)
    # naive v1 cost: 8 siblings each ingesting the full blob
    v1_would_be = 8 * blob_tokens

    def run_fn(wf, args):
        wf.phase("scan")
        ref = wf.block(blob)
        return wf.parallel([
            lambda i=i: wf.agent(
                f"task {i}: {ref}\nunique instruction for sibling {i}",
                label=f"t{i}",
                model="echo",
            )
            for i in range(8)
        ])

    report = run_workflow(
        run_fn=run_fn,
        args={},
        run_id="perf-dedup-structural",
        script_id="s",
        slug="t",
    )
    assert report["status"] == "completed"

    dr = dedup_report(run_dir_for("perf-dedup-structural"))
    assert dr["dedup_rate"] >= 0.70, (
        f"dedup_rate {dr['dedup_rate']} < 0.70 "
        f"(charged={dr['shared_tokens_charged']}, would_be={dr['shared_tokens_would_be']})"
    )

    # Budget charged the block exactly once (one scope "run"), not per sibling.
    leaves_dir = run_dir_for("perf-dedup-structural") / "leaves"
    leaves = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in leaves_dir.glob("*.json")
    ]
    leaf_in = sum(l["input_tokens"] for l in leaves)
    # Total spend = per-sibling ref-envelope cost + block charged once.
    assert report["spend"]["input_tokens"] == leaf_in + blob_tokens, (
        "block was not charged exactly once"
    )
    # V1 would have been at least 7x more expensive on the shared context alone.
    assert report["spend"]["input_tokens"] < v1_would_be, (
        "v2 spend should be far below v1 naive cost"
    )
