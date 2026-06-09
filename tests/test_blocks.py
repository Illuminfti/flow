"""WS-C: ArtifactRefDataflow (plan §6 L1/L2, §11.4) — block store, ref
substitution, charge-once budget accounting, Lever-1 measurement."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from flow import config
from flow.backends.pricing import estimate_tokens
from flow.blocks import BlockStore, block_scope, dedup_report, refs_in
from flow.loop import LoopSpec
from flow.paths import run_dir_for
from flow.runtime import run_workflow
from tests import _blocks_crash_helpers as bch

ECHO_CFG = {
    "engine": {"executor": "thread"},
    "leaf": {"allow_light_models": True, "noise_patterns": []},
    "providers": {"sh": {"kind": "shell_cmd", "cmd_template": ["echo", "{prompt}"]}},
    "models": {"echo": {"provider": "sh", "id": "echo", "in": 0.0, "out": 0.0,
                        "free": True, "caps": []}},
    "tiers": {"quality": ["echo"], "local": []},
    "defaults": {"tier": "quality"},
}

BLOB = "\n".join(f"ctx line {i} " + "x" * 40 for i in range(100))
BLOB_SHA = hashlib.sha256(BLOB.encode("utf-8")).hexdigest()
BLOB_TOKENS = estimate_tokens(BLOB)


def _echo_cfg(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)


def _journal(run_id):
    jp = run_dir_for(run_id) / "journal.jsonl"
    return [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()]


def _leaves(run_id):
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in (run_dir_for(run_id) / "leaves").glob("*.json")]


def _fanout_run(width):
    def run(wf, args):
        wf.phase("scan")
        ref = wf.block(BLOB)
        return wf.parallel([
            lambda i=i: wf.agent(f"task {i}: {ref}", label=f"t{i}", model="echo")
            for i in range(width)
        ])
    return run


def test_shared_block_stored_once(tmp_path):
    store = BlockStore(tmp_path)
    d1 = store.put(BLOB)
    mtime = store.path_for(BLOB_SHA).stat().st_mtime_ns
    d2 = store.put(BLOB)
    assert d1["sha256"] == d2["sha256"] == BLOB_SHA
    assert [p.name for p in store.dir.iterdir()] == [f"{BLOB_SHA}.txt"]
    assert store.path_for(BLOB_SHA).stat().st_mtime_ns == mtime  # idempotent, not rewritten
    assert store.read(BLOB_SHA) == BLOB
    assert d1["char_count"] == len(BLOB)
    assert d1["tokens_est"] == BLOB_TOKENS
    ref = d1["ref"]
    assert f"sha256={BLOB_SHA}" in ref
    assert "ctx line 0" in ref               # short summary
    assert "ctx line 99" not in ref          # not the full blob
    assert len(ref) < len(BLOB) // 4
    assert refs_in(f"a {ref} b {ref}") == [BLOB_SHA]


def test_siblings_get_refs_not_blob(tmp_path, monkeypatch):
    _echo_cfg(tmp_path, monkeypatch)
    rep = run_workflow(run_fn=_fanout_run(3), args={}, run_id="blocks-ref-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    # The model received the ref envelope, never the full blob.
    for text in rep["final"]:
        assert "[[flow-block" in text
        assert "ctx line 99" not in text
    leaves = _leaves("blocks-ref-001")
    assert len(leaves) == 3
    for l in leaves:
        assert l["block_refs"] == [BLOB_SHA]
        assert l["input_chars"] < len(BLOB) // 4
        assert l["input_sha256"] and len(l["input_sha256"]) == 64
    blocks_dir = run_dir_for("blocks-ref-001") / "artifacts" / "blocks"
    assert [p.name for p in blocks_dir.iterdir()] == [f"{BLOB_SHA}.txt"]


def test_block_charged_once_per_run_not_per_sibling(tmp_path, monkeypatch):
    _echo_cfg(tmp_path, monkeypatch)
    rep = run_workflow(run_fn=_fanout_run(4), args={}, run_id="blocks-charge-001",
                       script_id="s", slug="t")
    charges = [r for r in _journal("blocks-charge-001") if r["event"] == "block_charged"]
    assert len(charges) == 1
    assert charges[0]["sha256"] == BLOB_SHA
    assert charges[0]["scope"] == "run"
    assert charges[0]["input_tokens"] == BLOB_TOKENS
    assert "leaf_id" not in charges[0]  # progress/status aggregation ignores it
    # Spend = each sibling's own (ref-sized) input + the block ONCE — byte-exact.
    leaf_in = sum(l["input_tokens"] for l in _leaves("blocks-charge-001"))
    assert rep["spend"]["input_tokens"] == leaf_in + BLOB_TOKENS
    # v1 would have ingested the blob per sibling.
    assert rep["spend"]["input_tokens"] < leaf_in + 4 * BLOB_TOKENS


def test_block_charged_once_per_iteration(tmp_path, monkeypatch):
    _echo_cfg(tmp_path, monkeypatch)

    def step(wf, ctx):
        ref = wf.block(BLOB)
        return wf.parallel([
            lambda i=i: wf.agent(f"i{ctx['iteration']} t{i}: {ref}",
                                 label=f"i{ctx['iteration']}t{i}", model="echo")
            for i in range(3)
        ])

    def run(wf, args):
        wf.phase("work")
        return wf.loop(spec=LoopSpec(goal="g", max_iterations=2), step=step)

    rep = run_workflow(run_fn=run, args={}, run_id="blocks-iter-001",
                       script_id="s", slug="t")
    assert rep["status"] == "completed"
    charges = [r for r in _journal("blocks-iter-001") if r["event"] == "block_charged"]
    assert len(charges) == 2  # once per iteration, not once per sibling (6 refs)
    scopes = sorted(c["scope"] for c in charges)
    assert [s.split("/")[-1] for s in scopes] == ["i1", "i2"]
    assert all(c["sha256"] == BLOB_SHA for c in charges)
    leaf_in = sum(l["input_tokens"] for l in _leaves("blocks-iter-001"))
    assert rep["spend"]["input_tokens"] == leaf_in + 2 * BLOB_TOKENS
    # Each iteration's spend delta carries its own single block charge.
    for rec in rep["final"]["records"]:
        sibling_in = sum(l["input_tokens"] for l in rec["leaves"])
        assert rec["spend"]["input_tokens"] == sibling_in + BLOB_TOKENS


def test_resolve_blocks_round_trip(tmp_path, monkeypatch):
    _echo_cfg(tmp_path, monkeypatch)
    seen = {}

    def run(wf, args):
        wf.phase("p")
        ref = wf.block(BLOB)
        seen["ref"] = ref
        seen["resolved"] = wf.resolve_blocks(f"pre\n{ref}\npost")
        return wf.local(lambda: True, label="noop")

    run_workflow(run_fn=run, args={}, run_id="blocks-resolve-001", script_id="s", slug="t")
    assert seen["resolved"] == f"pre\n{BLOB}\npost"
    # On-demand path for CLI-agent backends: the envelope's path is readable.
    path = seen["ref"].split("path=", 1)[1].split("]]", 1)[0]
    assert Path(path).read_text(encoding="utf-8") == BLOB


def test_dedup_rate_measured_from_persisted_hashes(tmp_path, monkeypatch):
    _echo_cfg(tmp_path, monkeypatch)
    run_workflow(run_fn=_fanout_run(5), args={}, run_id="blocks-rate-001",
                 script_id="s", slug="t")
    report = dedup_report(run_dir_for("blocks-rate-001"))
    b = report["blocks"][BLOB_SHA]
    assert b["ref_count"] == 5
    assert b["charge_count"] == 1
    assert b["scopes"] == ["run"]
    assert b["tokens"] == BLOB_TOKENS
    assert report["shared_tokens_would_be"] == 5 * BLOB_TOKENS
    assert report["shared_tokens_charged"] == BLOB_TOKENS
    assert report["dedup_rate"] == 0.8  # measured, not inferred (Lever 1)
    assert report["leaf_count"] == 5
    assert report["leaves_with_input_hash"] == 5


def test_block_scope_extraction():
    assert block_scope("scan") == "run"
    assert block_scope("work/loop:abc123/i2/step") == "loop:abc123/i2"
    assert block_scope("work/loop:abc123/i2/verify") == "loop:abc123/i2"
    assert block_scope("w/loop:aaa/i1/step/loop:bbb/i3/step") == "loop:bbb/i3"


def test_resume_no_double_count_on_block_charges(tmp_path, monkeypatch):
    """Plan §11.4: on resume, the iteration's block charge is already in
    committed spend — replayed once, never re-charged by the rerun iteration."""
    data = tmp_path / "data"
    marker = tmp_path / "crashed"
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(ECHO_CFG))
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src"),
           "FLOW_DATA_DIR": str(data), "FLOW_CONFIG": str(cfgp)}
    proc = subprocess.run(
        [sys.executable, "-m", "tests._blocks_crash_helpers", str(marker)],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 137, proc.stderr
    assert marker.exists()

    run_dir = data / "runs" / bch.RUN_ID
    blob_tokens = estimate_tokens(bch.BLOB)
    pre_leaves = [json.loads(p.read_text(encoding="utf-8"))
                  for p in (run_dir / "leaves").glob("*.json")]
    assert len(pre_leaves) == 3  # w1a + w1b + w2a committed before the crash
    pre_leaf_in = sum(l["input_tokens"] for l in pre_leaves)

    monkeypatch.setenv("FLOW_DATA_DIR", str(data))
    monkeypatch.setenv("FLOW_CONFIG", str(cfgp))
    config.get(reload=True)
    rep = run_workflow(run_fn=bch.build_run(str(marker)), run_id=bch.RUN_ID,
                       script_id=bch.SCRIPT_ID, slug="blocks", max_workers=1)
    assert rep["status"] == "completed"
    assert rep["final"]["iterations"] == 2
    assert rep["final"]["replayed"] == 1  # iteration 1 replayed; 2 rerun off the leaf cache

    records = [json.loads(l) for l in
               (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    charges = [r for r in records if r.get("event") == "block_charged"]
    # One per iteration across BOTH lifetimes: i2's rerun dispatched a FRESH
    # sibling (w2b) referencing the block, and still did not re-charge.
    assert len(charges) == 2
    assert sorted(c["scope"].split("/")[-1] for c in charges) == ["i1", "i2"]
    replays = [r for r in records if r.get("event") == "budget_replay"]
    assert len(replays) == 1
    assert replays[0]["leaves"] == 3
    assert replays[0]["blocks"] == 2
    assert replays[0]["replayed_spend"]["input_tokens"] == pre_leaf_in + 2 * blob_tokens
    # Total spend across both lifetimes: each leaf once + each charge once.
    final_leaves = [json.loads(p.read_text(encoding="utf-8"))
                    for p in (run_dir / "leaves").glob("*.json")]
    assert len(final_leaves) == 4  # + w2b from the rerun
    assert rep["spend"]["input_tokens"] == (
        sum(l["input_tokens"] for l in final_leaves) + 2 * blob_tokens)
