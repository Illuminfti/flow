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
