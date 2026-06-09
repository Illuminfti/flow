"""WS-D/E/G acceptance: gates in the loop, goal_met/no_progress stops, repair
routing, handoff reports (plan §5.2)."""
import json

from flow.gates import GoalContract
from flow.loop import LoopSpec
from flow.paths import run_dir_for
from flow.runtime import run_workflow


def _journal(run_id):
    jp = run_dir_for(run_id) / "journal.jsonl"
    return [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()]


def _run(spec, step, verify, run_id, **kw):
    def run_fn(wf, args):
        wf.phase("work")
        return wf.loop(spec=spec, step=step, verify=verify)
    rep = run_workflow(run_fn=run_fn, args={}, run_id=run_id, script_id="s",
                       slug="t", **kw)
    return rep, rep["final"]


def _step(wf, ctx):
    return wf.local(lambda i: {"i": i}, ctx["iteration"], label=f"step{ctx['iteration']}")


def test_goal_met_stops_early_and_saves_budget():
    """§5.3 test_early_stop_budget_savings: goal met at iteration 2 of 5 →
    ≤45% of the no-early-stop call ceiling is spent."""
    def verify(wf, ctx):
        i = ctx["candidate"]["i"]
        verdict = {"verdict": "accept" if i >= 2 else "reject", "issues": []}
        return wf.local(lambda v: v, verdict, label=f"verify{i}")

    spec = LoopSpec(goal="g", max_iterations=5, required_gates=["schema", "verifier"])
    rep, final = _run(spec, _step, verify, "loop-goal-001")
    assert final["stop_reason"] == "goal_met"
    assert final["status"] == "completed"
    assert final["goal_met"] is True
    assert final["iterations"] == 2
    # 2 of 5 iterations × 2 leaves each = 4 calls vs the 10-call full-loop shape
    assert final["spend"]["calls"] <= 0.45 * (5 * 2)
    assert final["handoff"]["next_bounded_action"].startswith("none — goal met")
    rec = final["records"][-1]
    assert rec["gate_results"]["verifier"]["passed"]
    assert rec["gate_results"]["schema"]["passed"]


def test_goal_unmet_at_cap_is_exhausted_not_completed():
    def verify(wf, ctx):
        return wf.local(lambda: {"verdict": "reject", "issues": ["nope"]},
                        label=f"verify{ctx['iteration']}")

    spec = LoopSpec(goal="g", max_iterations=2, required_gates=["verifier"],
                    stop_conditions=("goal_met", "max_iterations"))
    rep, final = _run(spec, _step, verify, "loop-goal-002")
    assert final["stop_reason"] == "max_iterations"
    assert final["status"] == "exhausted"
    assert final["goal_met"] is False
    assert "max_iterations" in final["handoff"]["next_bounded_action"] \
        or "resume" in final["handoff"]["next_bounded_action"]


def test_plain_bounded_loop_keeps_v1_semantics():
    """No gates, no contract → max_iterations with a completed last iteration
    stays status=completed (back-compat)."""
    spec = LoopSpec(goal="g", max_iterations=2)
    rep, final = _run(spec, _step, None, "loop-goal-003")
    assert final["status"] == "completed"
    assert final["stop_reason"] == "max_iterations"
    assert final["goal_met"] is False


def test_repeated_failure_stops_before_budget_burn():
    """§5.2: identical rejections stall the loop long before the cap."""
    def verify(wf, ctx):
        return wf.local(lambda: {"verdict": "reject", "issues": ["same issue"]},
                        label=f"verify{ctx['iteration']}")

    def static_step(wf, ctx):
        return wf.local(lambda: {"same": "candidate"}, label=f"step{ctx['iteration']}")

    spec = LoopSpec(goal="g", max_iterations=10, required_gates=["verifier"],
                    stall_limit=2)
    rep, final = _run(spec, static_step, verify, "loop-stall-001")
    assert final["stop_reason"] == "no_progress"
    assert final["status"] == "stalled"
    assert final["iterations"] == 2  # stalled at the limit, 8 iterations unburned
    assert "stall" in final["handoff"]["next_bounded_action"]
    events = [r["event"] for r in _journal("loop-stall-001")]
    assert "failure_signature" in events


def test_changed_candidates_do_not_stall():
    def verify(wf, ctx):
        return wf.local(lambda: {"verdict": "reject", "issues": ["same issue"]},
                        label=f"verify{ctx['iteration']}")

    spec = LoopSpec(goal="g", max_iterations=3, required_gates=["verifier"],
                    stall_limit=2)
    rep, final = _run(spec, _step, verify, "loop-stall-002")
    # candidate changes each iteration → new evidence → runs to the cap
    assert final["stop_reason"] == "max_iterations"
    assert final["iterations"] == 3


def test_malformed_verifier_output_repairs_or_fails():
    """§5.2: RepairRouter selects verifier_repair on a parse failure; one
    bounded re-ask; success on repair."""
    calls = {"n": 0}

    def verify(wf, ctx):
        calls["n"] += 1
        if ctx.get("repair"):
            return wf.local(lambda: {"verdict": "accept", "issues": []},
                            label=f"verify-repair{ctx['iteration']}")
        return "this is not a verdict object"

    spec = LoopSpec(goal="g", max_iterations=3, required_gates=["verifier"])
    rep, final = _run(spec, _step, verify, "loop-repair-001")
    assert final["stop_reason"] == "goal_met"
    rec = final["records"][-1]
    assert rec["gate_results"]["verifier"]["passed"]
    assert rec["repairs"] >= 1
    assert calls["n"] == 2  # original + one repair


def test_malformed_verifier_repair_exhaustion_fails_gate():
    def verify(wf, ctx):
        return "still not a verdict"

    spec = LoopSpec(goal="g", max_iterations=2, required_gates=["verifier"],
                    stall_limit=2)
    rep, final = _run(spec, _step, verify, "loop-repair-002")
    assert final["goal_met"] is False
    rec = final["records"][-1]
    g = rec["gate_results"]["verifier"]
    assert not g["passed"] and g["verdict"] == "error"
    assert rec["repairs"] == 1  # bounded by max_repairs_per_iteration


def test_budget_exhaustion_emits_handoff():
    """§5.2: an exhausted loop hands off a bounded next action, never a bare
    failed."""
    def verify(wf, ctx):
        return wf.local(lambda: {"verdict": "reject", "issues": ["more work"]},
                        label=f"verify{ctx['iteration']}")

    spec = LoopSpec(goal="g", max_iterations=10, required_gates=["verifier"],
                    budget={"max_calls": 3}, stall_limit=10)
    rep, final = _run(spec, _step, verify, "loop-handoff-001")
    assert final["stop_reason"] == "budget_exhausted"
    assert final["status"] == "exhausted"
    handoff = final["handoff"]
    assert "resume" in handoff["next_bounded_action"]
    assert handoff["budget_remaining"] is not None
    run_dir = run_dir_for("loop-handoff-001")
    on_disk = json.loads((run_dir / "handoff-report.json").read_text(encoding="utf-8"))
    assert on_disk["next_bounded_action"] == handoff["next_bounded_action"]
    assert on_disk["rejected"] and not on_disk["verified"]
    events = [r["event"] for r in _journal("loop-handoff-001")]
    assert "handoff_written" in events


def test_stop_is_deterministic_on_replay():
    """I3: same journal → same stop_reason at the same iteration."""
    def verify(wf, ctx):
        return wf.local(lambda: {"verdict": "reject", "issues": ["same issue"]},
                        label=f"verify{ctx['iteration']}")

    def static_step(wf, ctx):
        return wf.local(lambda: {"same": "candidate"}, label=f"step{ctx['iteration']}")

    spec = LoopSpec(goal="g", max_iterations=10, required_gates=["verifier"],
                    stall_limit=2)
    rep1, final1 = _run(spec, static_step, verify, "loop-replay-001")
    rep2, final2 = _run(spec, static_step, verify, "loop-replay-001")
    assert final1["stop_reason"] == final2["stop_reason"] == "no_progress"
    assert final1["iterations"] == final2["iterations"]
    assert final2["replayed"] == final2["iterations"]  # all from the journal


def test_loopspec_change_invalidates_affected_cache_only():
    """§5.2: a spec change yields a new loop identity — the loop's iterations
    rerun, while work outside the loop still short-circuits via the leaf
    cache."""
    def run_fn(spec):
        def fn(wf, args):
            wf.phase("pre")
            wf.local(lambda: "outside-the-loop", label="pre-work")
            wf.phase("work")
            return wf.loop(spec=spec, step=_step, verify=None)
        return fn

    spec_a = LoopSpec(goal="g", max_iterations=2)
    rep1 = run_workflow(run_fn=run_fn(spec_a), args={}, run_id="loop-spec-001",
                        script_id="s", slug="t")
    spec_b = LoopSpec(goal="g CHANGED", max_iterations=2)
    rep2 = run_workflow(run_fn=run_fn(spec_b), args={}, run_id="loop-spec-001",
                        script_id="s", slug="t")
    final1, final2 = rep1["final"], rep2["final"]
    assert final1["loop_id"] != final2["loop_id"]
    assert final2["replayed"] == 0  # loop iterations not replayed across spec change
    # the unaffected pre-loop leaf cache-hit on the second run
    cached = [r for r in _journal("loop-spec-001")
              if r.get("event") == "cached" and r.get("label") == "pre-work"]
    assert cached


def test_custom_goal_contract_predicate():
    contract = GoalContract(
        predicate=lambda rec: (rec.get("candidate") or {}).get("i", 0) >= 3,
        description="candidate i >= 3")
    spec = LoopSpec(goal="g", max_iterations=5, goal_contract=contract)
    rep, final = _run(spec, _step, None, "loop-contract-001")
    assert final["stop_reason"] == "goal_met"
    assert final["iterations"] == 3


def test_step_receives_prev_gate_feedback():
    seen = {}

    def verify(wf, ctx):
        return wf.local(lambda: {"verdict": "reject", "issues": ["needs i>=2"]},
                        label=f"verify{ctx['iteration']}")

    def step(wf, ctx):
        if ctx["iteration"] == 2:
            seen["prev_gates"] = ctx["prev_gates"]
        return wf.local(lambda i: {"i": i}, ctx["iteration"],
                        label=f"step{ctx['iteration']}")

    spec = LoopSpec(goal="g", max_iterations=2, required_gates=["verifier"])
    _run(spec, step, verify, "loop-feedback-001")
    assert seen["prev_gates"]["verifier"]["passed"] is False
    assert seen["prev_gates"]["verifier"]["issues"] == ["needs i>=2"]


def test_finalize_does_not_mislabel_completed():
    """§3.3 WS-G: all leaves completed + finalize raises → never a bare
    `failed` with error=None."""
    def run_fn(wf, args):
        wf.local(lambda: 42, label="work", required=True)
        raise RuntimeError("finalize blew up")

    rep = run_workflow(run_fn=run_fn, args={}, run_id="loop-final-001",
                       script_id="s", slug="t")
    assert rep["status"] != "failed"
    assert rep["status"] in ("completed", "completed_with_warnings")
    assert rep.get("finalization_error")

    def run_fn_bad(wf, args):
        raise RuntimeError("true failure before any leaf")

    rep2 = run_workflow(run_fn=run_fn_bad, args={}, run_id="loop-final-002",
                        script_id="s", slug="t")
    assert rep2["status"] == "failed"
    assert rep2.get("error")  # a true terminal failure always carries an error
