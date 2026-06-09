# Python API

## `run_workflow`

```python
from flow import run_workflow

report = run_workflow(
    run_fn=run,
    args={"target": "src"},
    budget={"max_usd": 1, "max_calls": 8},
    run_id="optional-stable-id",
    executor_kind="thread",
    max_workers=8,
)
```

`run_fn` receives `(wf, args)` and returns the final result. Pass `script_path="workflow.py"` instead of `run_fn` to load a workflow file.

## `wf.agent`

```python
wf.agent(
    prompt,
    label="review:security",
    schema=JSON_SCHEMA,
    tier="quality",
    model=None,
    provider=None,
    backend=None,
    max_tokens=1000,
    timeout=120,
    tools=["tool_name"],
    tool_approval_gates={"tool_name": approval_callback},
    required=True,
)
```

Runs one model leaf. If `schema` is supplied, the backend output is parsed and repaired once when invalid. If `required=False`, a failed leaf returns `None` instead of raising.

## `wf.local`

```python
wf.local(fn, *args, label="local-step", schema=None, **kwargs)
```

Runs deterministic Python as a leaf. Use this for collection, reduction, local validation, and no-model transforms.

## Combinators

```python
wf.parallel([lambda: step_a(), lambda: step_b()], mode="lenient")
wf.pipeline(items, stage1, stage2, mode="collect_errors")
wf.workflow(child_fn, inputs, label="child")
```

Modes:

- `lenient`: failed item becomes `None`.
- `fail_fast`: first completed failure raises `ParallelError` and cancels pending siblings.
- `collect_errors`: returns `ExecutionResult(ok, value, error, index)` envelopes.

Pipeline stages can accept `cur`, `cur,item`, `cur,item,idx`, or `*args`.

## Tools

Register tools with `register_tool(ToolDefinition(...))`, then grant by name per leaf with `tools=[...]`. Backends without a native tool loop fail closed rather than silently dropping tool grants.

## Custom backends

Use `register_backend(kind, builder)` to add a provider implementation. See [backends.md](backends.md).

## `wf.code` (v3)

```python
receipt = wf.code(
    prompt,
    *,
    agent="coder",        # name of the agents: config entry
    label=None,           # leaf label for traces/resume
    workspace=None,       # cwd for the harness; defaults to os.getcwd()
    isolation=None,       # "" (default) or "worktree"
    schema=None,          # JSON Schema dict for the final answer
    continue_id="",       # session_id from a prior receipt → resume that session
    reserve_tokens=None,  # override agents.<name>.reserve_tokens for this leaf
    timeout=None,         # override agents.<name>.timeout_s for this leaf
    required=True,        # False → return None on failure instead of raising
)
```

Returns a receipt dict: `value` (schema-parsed answer), `text`, `session_id`
(continuation handle), `patch` (`{changed, files, patch}` — populated when
`isolation="worktree"`), `workspace`, `leaf_id`, `agent`, `spend`
(`{input_tokens, output_tokens, usd}`).

Agents are configured in the `agents:` config section — harness, sandbox,
allowed_tools, reserve_tokens, timeout_s, bin, system_prompt, cmd_template.
See [`agents.md`](agents.md) for the full reference.

## `wf.loop` (v2)

```python
from flow.loop import LoopSpec

result = wf.loop(
    spec=LoopSpec(
        goal="produce a valid section",
        max_iterations=5,
        budget={"max_tokens": 50000},   # loop-scoped ceiling, independent of run budget
        required_gates=["schema", "verifier"],
        verifier_policy={"tier": "quality", "must_differ_from_executor": True},
        stop_conditions=("goal_met", "no_progress", "budget_exhausted", "max_iterations"),
        stall_limit=2,
        goal_contract=None,             # None → default_goal_contract(required_gates)
        max_repairs_per_iteration=1,
    ),
    step=step_fn,                       # Callable[[Workflow, dict], Any]
    verify=verify_fn,                   # Optional[Callable[[Workflow, dict], Any]]
    gate_runner=None,                   # Optional[GateRunner]; default GateRunner() used when None
)
# result is a LoopRun receipt dict:
# result["goal_met"], result["stop_reason"], result["status"],
# result["candidate"], result["iterations"], result["spend"],
# result["records"], result["handoff"]
```

`step` and `verify` receive a context dict. Key fields: `goal`, `iteration`,
`prev`, `prev_gates`, `prev_failures`, `candidate` (verify only),
`repair` + `previous` (verify repair turn only).

Returns a `LoopRun` dict. `result["handoff"]` is a `HandoffReport` dict with
`next_bounded_action`, `verified`, `rejected`, `failure_signatures`,
`budget_remaining`. The same report is written to `handoff-report.json` in the
run dir.

Full guide: [loop.md](loop.md).

## `wf.block` / `wf.resolve_blocks` (v2)

```python
# Store shared context once; returns a ref envelope string.
ref = wf.block(text, summary_chars=240)

# Embed ref in prompts to siblings — budget charged once per iteration scope.
result = wf.agent(f"Context: {ref}\n\nDo the work.", label="leaf")

# Expand ref envelopes back to full content (in-process consumers).
full_text = wf.resolve_blocks(ref)
```

`wf.block` is sha256-addressed and idempotent. CLI-agent backends read the
envelope's embedded path directly; in-process consumers call `wf.resolve_blocks`.
`dedup_report(run_dir)` measures the saved re-ingestion across a run.

## New public exports (v2)

```python
from flow import (
    GateResult,
    GateRunner,
    GoalContract,
    default_goal_contract,
    HandoffReport,
    FailureSignature,
    FailureSignatureRegistry,
    RepairRouter,
)
```

`GateRunner.register(gate_id, fn, *, tier="deterministic")` — add a custom gate.
`default_goal_contract(required_gates)` — returns a `GoalContract` that requires
all named gates to pass; never vacuously fires with an empty gate list.
`FailureSignatureRegistry.from_records(records)` — rebuild deterministically from
iteration records. `RepairRouter(overrides={})` — extend or override the
`error_kind → strategy` table.
