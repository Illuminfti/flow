# Loop envelope

`wf.loop` is a bounded, crash-resumable iterate-to-goal envelope layered on top
of the v1 DAG executor. Each iteration runs `step` then (optionally) `verify` as
ordinary DAG leaves, then evaluates the spec's required acceptance gates and the
goal contract. Completed iterations are journaled and never rerun after a crash.
Every stop — success or otherwise — writes a `HandoffReport` with a concrete
bounded next action.

## Quick example

```python
from flow import run_workflow
from flow.loop import LoopSpec


SPEC = LoopSpec(
    goal="produce a valid, self-consistent report section",
    max_iterations=5,
    required_gates=["schema", "verifier"],
    stall_limit=2,
)

SECTION_SCHEMA = {
    "type": "object",
    "required": ["title", "body"],
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
}


def run(wf, args):
    def step(wf, ctx):
        return wf.agent(
            f"Write the section. Iteration {ctx['iteration']}. "
            f"Previous gate feedback: {ctx['prev_gates']}",
            label=f"draft:{ctx['iteration']}",
            schema=SECTION_SCHEMA,
            tier="quality",
        )

    def verify(wf, ctx):
        return wf.agent(
            f"Review for factual consistency: {ctx['candidate']}. "
            "Reply with JSON: {\"verdict\": \"accept\" | \"reject\", \"issues\": [...]}",
            label=f"verify:{ctx['iteration']}",
            tier="quality",
        )

    return wf.loop(spec=SPEC, step=step, verify=verify)


if __name__ == "__main__":
    report = run_workflow(run_fn=run, args={},
                          budget={"max_usd": 2, "max_calls": 20})
    final = report["final"]
    print(final["stop_reason"], final["goal_met"])
    print(final["handoff"]["next_bounded_action"])
```

## `wf.loop` signature

```python
wf.loop(
    *,
    spec: LoopSpec,
    step: Callable[[Workflow, dict], Any],
    verify: Optional[Callable[[Workflow, dict], Any]] = None,
    gate_runner: Optional[GateRunner] = None,
) -> dict   # LoopRun receipt
```

`step` and `verify` receive a context dict with:

| key             | value                                                                            |
| --------------- | -------------------------------------------------------------------------------- |
| `goal`          | the spec's goal string                                                           |
| `iteration`     | current iteration number (1-based)                                               |
| `prev`          | last accepted candidate, or `None` on iteration 1                                |
| `prev_gates`    | gate results from the previous iteration (`{}` on iteration 1)                   |
| `prev_failures` | failure records from the previous iteration                                      |
| `candidate`     | (`verify` only) the candidate produced by `step` in this iteration               |
| `repair`        | (`verify` only, repair turn) the parse error from the malformed previous verdict |

## `LoopSpec` fields

| field                       | default  | description                                                                                    |
| --------------------------- | -------- | ---------------------------------------------------------------------------------------------- |
| `goal`                      | required | human-readable goal string, carried into context and handoff                                   |
| `max_iterations`            | `5`      | hard ceiling; the loop always terminates at or before this                                     |
| `budget`                    | `None`   | loop-scoped ceiling dict (`max_tokens`, `max_usd`, `max_calls`); independent of the run budget |
| `required_gates`            | `()`     | gate ids run by `GateRunner` each iteration                                                    |
| `verifier_policy`           | `None`   | `{"tier": ..., "must_differ_from_executor": bool}`                                             |
| `stop_conditions`           | all four | which conditions are active; `max_iterations` and `budget_exhausted` are always enforced       |
| `stall_limit`               | `2`      | consecutive evidence-identical unaccepted iterations before `no_progress`                      |
| `goal_contract`             | `None`   | custom `GoalContract`; default: all required gates pass                                        |
| `max_repairs_per_iteration` | `1`      | bounded `RepairRouter` retries per iteration                                                   |

## LoopRun receipt

`wf.loop` returns a plain dict with these keys:

| key           | description                                                               |
| ------------- | ------------------------------------------------------------------------- |
| `loop_id`     | stable identifier for this loop (hash of script + phase + spec)           |
| `goal`        | the spec's goal string                                                    |
| `spec_hash`   | hash of the LoopSpec identity                                             |
| `status`      | `completed` \| `exhausted` \| `stalled` \| `cancelled` \| `failed`        |
| `stop_reason` | one of the five stop conditions below                                     |
| `goal_met`    | `True` only when `stop_reason == "goal_met"`                              |
| `iterations`  | number of iterations run (including replayed)                             |
| `replayed`    | iterations loaded from the journal on this execution                      |
| `candidate`   | the last accepted candidate value, or the last produced candidate         |
| `spend`       | `{input_tokens, output_tokens, tokens, usd, calls}` across all iterations |
| `records`     | list of `IterationRecord` dicts                                           |
| `handoff`     | `HandoffReport` dict                                                      |

## Stop precedence

The stop condition is evaluated after each iteration in this fixed order:

1. `goal_met` — the goal contract is satisfied
2. `max_iterations` — the hard cap is reached
3. `no_progress` — stall detection triggered
4. `budget_exhausted` — loop or run budget is at the ceiling
5. `cancelled` — run cancel flag is set (Ctrl+C, SIGTERM, deadline)

This is a pure function of the iteration records. The same journal always replays
to the same `stop_reason` at the same iteration (invariant I3).

`max_iterations` and `budget_exhausted` are enforced regardless of which stop
conditions are listed in `spec.stop_conditions`. A declared-bounded loop cannot
be made unbounded by omitting them from the list.

**Status vs stop_reason mapping:**

| stop_reason                                        | status                                                 |
| -------------------------------------------------- | ------------------------------------------------------ |
| `goal_met`                                         | `completed`                                            |
| `max_iterations` + goal contract declared          | `exhausted`                                            |
| `max_iterations` + no goal contract (v1 semantics) | `completed` if last iteration succeeded, else `failed` |
| `no_progress`                                      | `stalled`                                              |
| `budget_exhausted`                                 | `exhausted`                                            |
| `cancelled`                                        | `cancelled`                                            |

## Gates and tiers

Gates run after each iteration's `step` + `verify` leaves complete. Built-in gates:

| gate id      | tier          | what it checks                                              |
| ------------ | ------------- | ----------------------------------------------------------- |
| `"schema"`   | deterministic | every leaf in the iteration completed with no schema errors |
| `"artifact"` | deterministic | `step` produced a non-None candidate                        |
| `"verifier"` | verifier      | `verify` returned `{"verdict": "accept", "issues": []}`     |

**Tier order:** `GateRunner` runs deterministic gates first (free). Verifier-tier
gates are **skipped** — not charged — when any required deterministic gate has
already failed. Never spend tokens grading a candidate that cannot be accepted.

Register custom gates:

```python
from flow.gates import GateResult, GateRunner

runner = GateRunner()

def my_length_check(ctx: dict) -> GateResult:
    candidate = ctx.get("candidate") or {}
    ok = len(candidate.get("body", "")) >= 100
    return GateResult(
        gate_id="min_length",
        passed=ok,
        tier="deterministic",
        verdict="accept" if ok else "reject",
        issues=[] if ok else [{"error": "body too short"}],
    )

runner.register("min_length", my_length_check, tier="deterministic")

result = wf.loop(spec=LoopSpec(goal="g", required_gates=["schema", "min_length"]),
                 step=step, gate_runner=runner)
```

## Goal contracts

A `GoalContract` is a boolean predicate over one iteration's record dict. It
determines when `goal_met` fires.

**Default contract** (`default_goal_contract`): all required gates passed and the
iteration status is `completed`. A loop with no gates and no contract can **never**
vacuously fire `goal_met` — the predicate requires at least one gate result.

```python
from flow.gates import GoalContract

# Custom: accept as soon as candidate score >= 0.9
contract = GoalContract(
    predicate=lambda rec: (rec.get("candidate") or {}).get("score", 0) >= 0.9,
    description="candidate score >= 0.9",
)
spec = LoopSpec(goal="high-quality draft", max_iterations=8,
                goal_contract=contract)
```

When both `required_gates` and `goal_contract` are set, the custom contract
takes precedence. Set `goal_contract` only when the default all-gates predicate
is not the right shape.

## Stall detection

`is_stalled` fires `no_progress` when the last `stall_limit` iterations are all:

1. evidence-identical: same candidate content + same failure signatures + same
   gate pass/fail shape
2. unaccepted: iteration `status != "completed"` or at least one gate failed

An all-green window is not a stall — `goal_met` (higher precedence) or
`max_iterations` owns that outcome.

Adjust the threshold:

```python
spec = LoopSpec(goal="g", max_iterations=10, required_gates=["verifier"],
                stall_limit=3)  # require 3 identical failures before stopping
```

## Repair routing

When `verify` returns a malformed value (not a `{"verdict": ..., "issues": [...]}`
dict), the `RepairRouter` selects a strategy:

- `parse_error` / `schema_error` on the `verifier` gate → `verifier_repair`: one
  bounded re-ask, with `ctx["repair"]` set to the parse error string and
  `ctx["previous"]` set to the bad output
- `budget:` prefix → `none`: budget breaches are never repaired by retrying
- other `runtime_error` → `retry` (leaf-level, handled by the scheduler)

The repair count is bounded by `max_repairs_per_iteration`. After exhaustion, the
gate records `verdict="error"` and the iteration is rejected.

```python
def verify(wf, ctx):
    if ctx.get("repair"):
        # Called again with the parse error attached.
        # Fix the output and return a valid verdict.
        return wf.agent(
            f"Your previous verdict was malformed: {ctx['repair']}. "
            f"Prior output: {ctx['previous']}. Return valid JSON verdict.",
            label=f"verify-repair:{ctx['iteration']}",
        )
    return wf.agent("Review and return verdict JSON.", ...)
```

## Handoff report

Every loop stop writes `handoff-report.json` in the run dir and attaches the
same dict as `result["handoff"]`. Keys:

| key                   | description                                                                    |
| --------------------- | ------------------------------------------------------------------------------ |
| `loop_id`             | loop identifier                                                                |
| `run_id`              | run identifier                                                                 |
| `goal`                | spec goal string                                                               |
| `status`              | loop status                                                                    |
| `stop_reason`         | stop condition that fired                                                      |
| `goal_met`            | bool                                                                           |
| `iterations`          | total iterations                                                               |
| `replayed`            | iterations loaded from journal                                                 |
| `verified`            | list of iterations where all gates passed                                      |
| `rejected`            | list of iterations with a failed or skipped gate                               |
| `failure_signatures`  | top 5 recurring failures: `{error_kind, message, gate_id, fingerprint, count}` |
| `spend`               | total spend across all iterations                                              |
| `budget_remaining`    | remaining budget at loop stop                                                  |
| `next_bounded_action` | a concrete bounded next step (e.g. `flow resume <run_id>`)                     |

The `next_bounded_action` is always a concrete, actionable string:

- `goal_met` → `"none — goal met; consume the accepted candidate"`
- `budget_exhausted` → `"raise the loop/run token ceiling and flow resume <run_id> — completed iterations replay free"`
- `no_progress` → describes the stall and the recurring failure
- `cancelled` / `max_iterations` → a `flow resume <run_id>` command

## Crash-resume semantics

Completed iterations are journaled atomically (WAL-first). On resume:

- The engine rehydrates committed leaf spend and block charges from the journal
  so the budget ceiling is enforced correctly across N crash-resume cycles.
- Iterations with a `iteration_completed` WAL event are replayed from the record
  — `step` and `verify` are not re-executed.
- A mid-iteration crash reruns that iteration from scratch.
- Work outside the loop (leaves before/after `wf.loop`) follows normal
  leaf-level resume semantics.

```bash
flow run myworkflow.py --run-id my-loop-run
# crash
flow resume my-loop-run myworkflow.py
```

The `flow resume` command uses the manifest's persisted `args` and `budget`, so
the caller need not re-specify them.

## Verifier identity enforcement

Set `must_differ_from_executor: true` in `verifier_policy` to prevent a required
gate from self-grading. The check runs at loop start, before any iteration:

```python
spec = LoopSpec(
    goal="g",
    max_iterations=5,
    required_gates=["verifier"],
    verifier_policy={"tier": "quality", "must_differ_from_executor": True},
)
```

If `tier` in `verifier_policy` resolves to the same `(provider, model)` as the
executor tier, `VerifierIdentityError` is raised immediately and the
`verifier_identity_collision` WAL event is written. The loop never starts. This
fails closed — there is no silent downgrade to same-brain grading.

## Loop budget vs run budget

`LoopSpec.budget` is a loop-scoped ceiling independent of the run budget. Both
are enforced:

- The run budget gates every leaf reservation via `budget.reserve`.
- The loop budget is checked after each iteration against cumulative loop spend.
- `budget_exhausted` fires when either ceiling is reached.

```python
spec = LoopSpec(
    goal="g",
    max_iterations=20,
    budget={"max_tokens": 50000, "max_usd": 0.50},
)
report = run_workflow(run_fn=run, args={},
                      budget={"max_usd": 5.0, "max_calls": 100})
```

## WAL events

The loop emits these events to the run journal:

| event                         | when                                                     |
| ----------------------------- | -------------------------------------------------------- |
| `loop_started`                | before iteration 1                                       |
| `iteration_started`           | before each new iteration                                |
| `iteration_replayed`          | when an iteration is loaded from the journal             |
| `iteration_completed`         | after a successful iteration                             |
| `iteration_failed`            | after a failed iteration                                 |
| `failure_signature`           | for each failure fingerprint extracted from an iteration |
| `handoff_written`             | after the handoff report is written                      |
| `loop_stopped`                | after the final stop evaluation                          |
| `verifier_identity_collision` | when `must_differ_from_executor` check fails             |
| `budget_replay`               | at engine init when committed spend is rehydrated        |

## `IterationRecord` fields

Each element of `result["records"]` is an `IterationRecord` dict:

| field              | description                                                            |
| ------------------ | ---------------------------------------------------------------------- |
| `loop_id`          | loop identifier                                                        |
| `iteration`        | iteration number                                                       |
| `status`           | `completed` \| `failed`                                                |
| `context_hash`     | hash of goal + iteration + prev (deterministic context fingerprint)    |
| `inputs`           | `{goal, prev_hash}`                                                    |
| `candidate`        | value returned by `step`                                               |
| `verifier_verdict` | raw value returned by `verify` (or `None` if omitted)                  |
| `gate_results`     | dict of gate id → `GateResult` dict                                    |
| `leaves`           | list of leaf summaries for all leaves in this iteration                |
| `artifact_refs`    | artifact references from all leaves                                    |
| `repairs`          | total repair attempts across leaves and the verifier                   |
| `failures`         | list of failure records from leaves and exceptions                     |
| `spend`            | `{input_tokens, output_tokens, tokens, usd, calls}` for this iteration |
| `elapsed_s`        | wall time for this iteration                                           |
