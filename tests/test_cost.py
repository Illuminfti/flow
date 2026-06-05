"""Phase 3B: cost prediction via --dry-run --estimate-cost."""
from flow import authoring

SCRIPT = """
R = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
def run(wf, args):
    wf.phase("p")
    a = wf.parallel([(lambda i=i: wf.agent(f"q{i}", label=f"q{i}", tier="quality")) for i in range(3)])
    b = wf.agent("schema one", label="s", schema=R, tier="quality")
    return [a, b]
"""


def test_dry_run_estimate_cost(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    out = authoring.dry_run(SCRIPT, estimate_cost=True)
    assert out["ok"] and out["leaf_count"] == 4
    cost = out["cost"]
    assert cost["total_usd"] > 0
    assert len(cost["by_label"]) == 4
    # the schema leaf is budgeted at 2x — its est_usd should exceed a plain leaf's
    by = {c["label"]: c["est_usd"] for c in cost["by_label"]}
    assert by["s"] >= by["q0"]


def test_dry_run_no_cost_keys_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path))
    out = authoring.dry_run(SCRIPT)  # estimate_cost defaults False
    assert "cost" not in out and out["leaf_count"] == 4
