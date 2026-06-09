# Architecture

`flow` separates orchestration from model work.

## Runtime layers

1. **Workflow script**: a Python module exposing `run(wf, args)`.
2. **Workflow API**: `wf.agent`, `wf.code`, `wf.local`, `wf.parallel`, `wf.pipeline`, `wf.workflow`.
3. **Orchestration pool**: runs Python thunks concurrently.
4. **Router**: chooses a route from tier, model, provider, backend, capabilities, and cost.
5. **Leaf pool**: executes model, shell, agent, or local leaves.
6. **Worktree layer**: isolates agent leaves in detached git worktrees; captures change evidence as patch artifacts.
7. **Journal**: records run events and terminal leaf results.
8. **Progress views**: `trace`, `status`, `watch`, reports, and run cards.

## Leaf identity

A completed leaf can be reused on resume only when the work identity matches. Identity includes script id, phase, label, prompt, model, backend, schema, and behavior-affecting optional fields such as provider, toolsets, tools, max tokens, and local schema when present.

## Failure model

`wf.parallel` and `wf.pipeline` default to `lenient`: failed branches become `None` and the workflow continues. Use `fail_fast` when any failure should abort siblings, or `collect_errors` when the caller wants an envelope per item.

Cancellation is cooperative. Ctrl+C, SIGTERM, or a deadline sets the run cancel flag. Already-running provider calls may finish, but pending work stops and unfinished leaves rerun on resume.

## Resume model

Resume is leaf-level, not a full graph snapshot. The script is executed again, but completed leaves with matching identities are returned from the journal instead of recomputed. This keeps the implementation small, inspectable, and robust across process crashes.

## v3: agent_cli backend, worktree layer, session continuation

Three additions in v3, each layered on the v1/v2 primitives.

**`agent_cli` backend.** `wf.code` resolves to the `agent_cli` backend via a
`Route` with `backend="agent_cli"`. `AgentCLIBackend` is a dataclass that holds
harness config (harness, model, sandbox, allowed_tools, skip_permissions,
system_prompt, cmd_template, bin, schema_file, continue_id, transcript_path)
and dispatches to `_run_codex`, `_run_claude`, or `_run_generic`. `stdin` is
detached (`subprocess.DEVNULL`) on all harness calls — CLI agents read non-TTY
stdin as additional input and hang under a runner otherwise. The full raw stream
is appended to `transcript_path` after every call; transcript failures are
silently swallowed (observability, not data).

**Worktree isolation.** `scheduler.submit_leaf` detects `req.isolation ==
"worktree"` and `req.route.backend == "agent_cli"`, then calls
`worktree.prepare(workspace, run_dir/worktrees/<leaf_id[:16]>)` before
dispatching and `worktree.finalize(workspace, wt_path, patch_file)` after.
`finalize` stages all changes with `git add -A`, diffs with `--binary --cached`,
writes the patch to `run_dir/artifacts/<leaf_id[:16]>.patch`, removes the
worktree, and returns `{changed, patch, files}`. The relative patch path is
stored on `LeafResult.patch`; the full path is reconstructed from `run_dir`.
`git worktree add/remove` calls are serialized within-process via `_GIT_LOCK`.

**Session continuation.** `AgentCLIBackend.session_id` is set from the harness
result after the first call. Repair turns (schema enforcement) and explicit
`continue_id` chains both set `resume_id = session_id or continue_id` before
building the argv — codex prepends `exec resume <thread_id>`, claude appends
`--resume <session_id>`. `continue_id` is folded into the leaf identity hash
via `stable_hash({"agent", "workspace", "isolation", "continue_id"})`, so
chained leaves are separate journal entries.

**`reserve_tokens` budget floor.** `scheduler.submit_leaf` uses
`max(est_in, reserve_tokens - est_out)` as the input estimate when
`req.reserve_tokens > 0`, ensuring the budget gate sees a realistic floor before
the harness launches. Real spend is committed from harness result tokens after
the run.

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
