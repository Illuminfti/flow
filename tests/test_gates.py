"""WS-D: GateRunner, GoalContract, tiered gates (plan §5.1, §6 L3)."""
import pytest

from flow.gates import (DETERMINISTIC, VERIFIER, GateResult, GateRunner,
                        GoalContract, default_goal_contract, normalize_verdict)


def _ctx(*, candidate="x", verdict=None, leaves=(), spec=None, iteration=1):
    return {"candidate": candidate, "verdict": verdict,
            "leaves": list(leaves), "spec": spec, "iteration": iteration}


def test_schema_gate_passes_on_clean_leaves():
    res = GateRunner().run(["schema"], _ctx(leaves=[
        {"leaf_id": "a", "status": "completed"},
        {"leaf_id": "b", "status": "cached"}]))
    assert res["schema"]["passed"] and res["schema"]["verdict"] == "accept"


def test_schema_gate_rejects_failed_leaf():
    res = GateRunner().run(["schema"], _ctx(leaves=[
        {"leaf_id": "a", "status": "schema_failed", "error": "missing key",
         "error_kind": "schema_error"}]))
    g = res["schema"]
    assert not g["passed"]
    assert g["issues"][0]["leaf_id"] == "a"


def test_artifact_gate():
    runner = GateRunner()
    assert runner.run(["artifact"], _ctx(candidate={"x": 1}))["artifact"]["passed"]
    assert not runner.run(["artifact"], _ctx(candidate=None))["artifact"]["passed"]


def test_verifier_gate_accepts_clean_verdict():
    res = GateRunner().run(["verifier"], _ctx(verdict={"verdict": "accept", "issues": []}))
    assert res["verifier"]["passed"] and res["verifier"]["tier"] == VERIFIER


def test_verifier_gate_rejects_on_issues():
    res = GateRunner().run(["verifier"], _ctx(
        verdict={"verdict": "accept", "issues": ["unresolved finding"]}))
    g = res["verifier"]
    assert not g["passed"] and g["issues"] == ["unresolved finding"]


def test_verifier_gate_rejects_reject():
    res = GateRunner().run(["verifier"], _ctx(verdict={"verdict": "reject"}))
    assert not res["verifier"]["passed"]


def test_verifier_gate_malformed_output_is_error_kind():
    res = GateRunner().run(["verifier"], _ctx(verdict="not json at all"))
    g = res["verifier"]
    assert not g["passed"] and g["verdict"] == "error"
    assert g["error_kind"] in ("parse_error", "schema_error")


def test_verifier_string_json_is_parsed():
    res = GateRunner().run(["verifier"], _ctx(
        verdict='{"verdict": "accept", "issues": []}'))
    assert res["verifier"]["passed"]


def test_tiered_skip_verifier_when_deterministic_fails():
    """Lever 3: never spend verifier tokens grading an unacceptable candidate."""
    res = GateRunner().run(["verifier", "schema"], _ctx(
        verdict={"verdict": "accept"},
        leaves=[{"leaf_id": "a", "status": "failed", "error": "boom"}]))
    assert not res["schema"]["passed"]
    assert res["verifier"]["verdict"] == "skipped"
    assert not res["verifier"]["passed"]


def test_unknown_gate_raises():
    with pytest.raises(KeyError):
        GateRunner().run(["nope"], _ctx())


def test_custom_gate_registration():
    runner = GateRunner()
    runner.register("len", lambda ctx: GateResult(
        gate_id="len", passed=len(str(ctx["candidate"])) < 5), tier=DETERMINISTIC)
    assert runner.run(["len"], _ctx(candidate="ok"))["len"]["passed"]
    assert not runner.run(["len"], _ctx(candidate="toolong"))["len"]["passed"]


def test_gate_exception_is_runtime_error_result():
    runner = GateRunner()
    runner.register("boom", lambda ctx: 1 / 0)
    g = runner.run(["boom"], _ctx())["boom"]
    assert not g["passed"] and g["error_kind"] == "runtime_error"


def test_normalize_verdict_shapes():
    assert normalize_verdict({"verdict": "ACCEPT"})[0]["verdict"] == "accept"
    assert normalize_verdict(None)[0] is None
    assert normalize_verdict(42)[0] is None
    assert normalize_verdict({"no": "verdict"})[0] is None


def test_default_goal_contract_requires_gates():
    """§5.1: a vacuous goal never auto-passes."""
    empty = default_goal_contract([])
    assert not empty.satisfied({"status": "completed", "gate_results": {}})
    contract = default_goal_contract(["schema", "verifier"])
    assert contract.satisfied({"status": "completed", "gate_results": {
        "schema": {"passed": True}, "verifier": {"passed": True}}})
    assert not contract.satisfied({"status": "completed", "gate_results": {
        "schema": {"passed": True}, "verifier": {"passed": False}}})
    assert not contract.satisfied({"status": "failed", "gate_results": {
        "schema": {"passed": True}, "verifier": {"passed": True}}})


def test_goal_contract_predicate_exception_is_false():
    c = GoalContract(predicate=lambda rec: 1 / 0, description="boom")
    assert not c.satisfied({})
