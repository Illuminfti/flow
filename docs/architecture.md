# Architecture

`flow` separates orchestration from model work.

## Runtime layers

1. **Workflow script**: a Python module exposing `run(wf, args)`.
2. **Workflow API**: `wf.agent`, `wf.local`, `wf.parallel`, `wf.pipeline`, `wf.workflow`.
3. **Orchestration pool**: runs Python thunks concurrently.
4. **Router**: chooses a route from tier, model, provider, backend, capabilities, and cost.
5. **Leaf pool**: executes model, shell, or local leaves.
6. **Journal**: records run events and terminal leaf results.
7. **Progress views**: `trace`, `status`, reports, and run cards.

## Leaf identity

A completed leaf can be reused on resume only when the work identity matches. Identity includes script id, phase, label, prompt, model, backend, schema, and behavior-affecting optional fields such as provider, toolsets, tools, max tokens, and local schema when present.

## Failure model

`wf.parallel` and `wf.pipeline` default to `lenient`: failed branches become `None` and the workflow continues. Use `fail_fast` when any failure should abort siblings, or `collect_errors` when the caller wants an envelope per item.

Cancellation is cooperative. Ctrl+C, SIGTERM, or a deadline sets the run cancel flag. Already-running provider calls may finish, but pending work stops and unfinished leaves rerun on resume.

## Resume model

Resume is leaf-level, not a full graph snapshot. The script is executed again, but completed leaves with matching identities are returned from the journal instead of recomputed. This keeps the implementation small, inspectable, and robust across process crashes.
