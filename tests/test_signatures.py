"""WS-E: failure signatures, stall detection, repair routing (plan §6 L6)."""
from flow.signatures import (FailureSignature, FailureSignatureRegistry,
                             RepairRouter, is_stalled, normalize_message,
                             signatures_from_record, stall_key)


def test_normalize_strips_volatile_parts():
    a = normalize_message("budget would breach: 906313+161364 > 900000")
    b = normalize_message("budget would breach: 12+34 > 56")
    assert a == b
    assert normalize_message("leaf deadbeef1234 failed") == normalize_message(
        "leaf cafebabe9999 failed")


def test_fingerprint_stable_and_distinct():
    s1 = FailureSignature("schema_error", "missing required keys: #", "verifier")
    s2 = FailureSignature("schema_error", "missing required keys: #", "verifier")
    s3 = FailureSignature("schema_error", "missing required keys: #", "schema")
    assert s1.fingerprint() == s2.fingerprint()
    assert s1.fingerprint() != s3.fingerprint()


def test_signatures_from_record_covers_failures_and_gates():
    rec = {
        "iteration": 2,
        "failures": [{"error": "step blew up at 0x1f", "error_kind": "runtime_error"}],
        "gate_results": {
            "verifier": {"passed": False, "verdict": "reject",
                         "issues": [{"error": "weak evidence"}]},
            "schema": {"passed": True},
        },
    }
    sigs = signatures_from_record(rec)
    kinds = {(s.error_kind, s.gate_id) for s in sigs}
    assert ("runtime_error", "") in kinds
    assert any(g == "verifier" for _, g in kinds)
    assert not any(g == "schema" for _, g in kinds)


def test_registry_counts_and_top():
    rec = {"iteration": 1, "failures": [{"error": "same failure", "error_kind": "runtime_error"}],
           "gate_results": {}}
    reg = FailureSignatureRegistry.from_records([rec, {**rec, "iteration": 2}])
    top = reg.top(1)
    assert top[0]["count"] == 2
    assert reg.iteration_fingerprints(1) == reg.iteration_fingerprints(2)


def test_stall_detection_identical_rejections():
    rec = {"iteration": 1, "status": "completed", "candidate": {"x": 1},
           "failures": [],
           "gate_results": {"verifier": {"passed": False, "verdict": "reject",
                                         "issues": [{"error": "same issue"}]}}}
    window = [rec, {**rec, "iteration": 2}]
    assert is_stalled(window, stall_limit=2)


def test_no_stall_when_evidence_changes():
    r1 = {"iteration": 1, "status": "completed", "candidate": {"x": 1},
          "failures": [], "gate_results": {"verifier": {"passed": False, "verdict": "reject"}}}
    r2 = {**r1, "iteration": 2, "candidate": {"x": 2}}
    assert not is_stalled([r1, r2], stall_limit=2)


def test_no_stall_on_accepted_window():
    rec = {"iteration": 1, "status": "completed", "candidate": {"x": 1},
           "failures": [], "gate_results": {"schema": {"passed": True}}}
    assert not is_stalled([rec, {**rec, "iteration": 2}], stall_limit=2)


def test_stall_requires_window_filled():
    rec = {"iteration": 1, "status": "failed", "candidate": None, "failures": [],
           "gate_results": {}}
    assert not is_stalled([rec], stall_limit=2)


def test_stall_key_deterministic_over_json_roundtrip():
    import json
    rec = {"iteration": 1, "status": "completed", "candidate": {"a": [1, 2]},
           "failures": [], "gate_results": {"schema": {"passed": False}}}
    assert stall_key(rec) == stall_key(json.loads(json.dumps(rec)))


def test_repair_router_routes():
    r = RepairRouter()
    assert r.route(FailureSignature("parse_error", "no json", "verifier")) == "verifier_repair"
    assert r.route(FailureSignature("schema_error", "missing key", "verifier")) == "verifier_repair"
    assert r.route(FailureSignature("runtime_error", "boom", "")) == "retry"
    assert r.route(FailureSignature("runtime_error", "budget: would breach", "")) == "none"
    assert r.route(FailureSignature("gate_reject", "weak evidence", "verifier")) == "none"


def test_repair_router_overrides():
    r = RepairRouter(overrides={"runtime_error": "none"})
    assert r.route(FailureSignature("runtime_error", "boom", "")) == "none"
