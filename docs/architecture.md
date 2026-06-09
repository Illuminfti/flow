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

## v2: loop layer, block store, budget replay

Three additions in v2, each layered on the v1 primitives without modifying them.

**Loop layer.** `wf.loop` runs `step` and `verify` as ordinary DAG leaves under
iteration-scoped phases. The `LoopLedger` extends the run journal with
iteration-level WAL events (`loop_started`, `iteration_started`,
`iteration_completed`, `iteration_failed`, `iteration_replayed`,
`failure_signature`, `handoff_written`, `loop_stopped`) and materializes one
`IterationRecord` file per completed iteration. The stop decision is a pure
function of those records, so the same journal always replays to the same
`stop_reason` (invariant I3). Completed iterations never rerun after a crash.

**Block store in the dispatch path.** `wf.block(text)` writes a sha256-addressed
blob to `artifacts/blocks/` once per run (idempotent by content). `Engine.submit_leaf`
calls `_charge_blocks` before budget reservation: each block sha referenced in a
leaf's prompt is charged to the budget once per iteration scope (keyed by
`(scope, sha)`), not once per sibling. The charge is journaled before it is
committed (WAL-first), so crash-resume replays committed truth exactly once.
Real-world runs measured 53–89% redundant shared-context input eliminated.

**Budget replay at engine init.** On `Engine.__init__`, after the leaf journal
cache is loaded, the engine replays the full journal for `block_charged` events
and accumulates their token costs into `budget._spend` alongside the leaf spend.
This ensures the run budget ceiling is enforced correctly across N crash-resume
cycles (≤1× the declared ceiling, not N×). The `budget_replay` WAL event records
the rehydrated spend for inspection.
