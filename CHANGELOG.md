# Changelog

## 2.0.0 — bounded loop envelope (2026-06-09)

The v1 DAG API is unchanged and regression-locked. v2 adds a new execution layer
layered on top of it.

**Measured performance contract** (replaying real failed v1 production runs
under their unchanged token ceilings, live): a 25-leaf research fan-out that
died at 906,313/900,000 tokens under v1 completed under v2 at 205,073 total
(input 822,120 → 135,619, −83.5%) with the loop stopping on `goal_met` via an
identity-distinct verifier; a second workload that breached its 300k ceiling
completed likewise. Shared-context dedup measured ≥70% from persisted per-leaf
input hashes.

- **`wf.loop(spec, step, verify, gate_runner)` — iterate-to-goal envelope.**
  Each iteration runs `step` then `verify` as ordinary DAG leaves (leaf-level
  cache, resume, and budget gating all apply). Returns a `LoopRun` receipt dict
  carrying `goal_met`, `stop_reason`, `status`, `iterations`, `candidate`,
  `spend`, `records`, and the attached `HandoffReport`.

- **`LoopSpec` — declarative loop configuration.**
  Fields: `goal`, `max_iterations`, `budget` (loop-scoped ceiling), `required_gates`,
  `verifier_policy`, `stop_conditions`, `stall_limit`, `goal_contract`,
  `max_repairs_per_iteration`.

- **Tiered acceptance gates + `GoalContract` (gates.py).** `GateRunner` runs
  required gates in tier order: deterministic gates (`"schema"`, `"artifact"`)
  run first and are free; verifier-tier (`"verifier"`) is skipped — not charged —
  when a required deterministic gate has already failed. `default_goal_contract`
  requires all named gates to pass; a loop with no gates and no contract can
  never vacuously fire `goal_met`. Custom gates via `runner.register()`.

- **Deterministic stop precedence.** Order: `goal_met` > `max_iterations` >
  `no_progress` > `budget_exhausted` > `cancelled`. A pure function of iteration
  records — the same journal always replays to the same `stop_reason` at the same
  iteration (invariant I3).

- **Failure signatures + stall detection (signatures.py).** `FailureSignature`
  fingerprints a recurring failure as `(error_kind, normalized_message, gate_id)`.
  `is_stalled()` fires `no_progress` when `stall_limit` consecutive
  evidence-identical (same candidate + same signatures + same gate shape)
  unaccepted iterations have elapsed, stopping the loop before the token wall.
  `FailureSignatureRegistry` aggregates counts deterministically from iteration
  records.

- **Repair routing (signatures.py).** `RepairRouter` routes a malformed verifier
  verdict to one bounded re-ask (`verifier_repair`) strategy, bounded by
  `max_repairs_per_iteration`. Budget breaches are never repaired by retrying.

- **`HandoffReport` (handoff.py).** Every loop stop — goal met, exhausted,
  stalled, cancelled, failed — writes `handoff-report.json` with a verified /
  rejected iteration split, top failure signatures, spend, `budget_remaining`,
  and `next_bounded_action` (a concrete bounded next step, e.g. a `flow resume`
  command). The consumer never receives a bare `failed` status.

- **Shared-context block store + artifact-ref dataflow (blocks.py).** `wf.block(text)`
  stores a shared blob once (sha256-addressed) and returns a ref envelope. Siblings
  embed the ref instead of the full blob; the block is charged to the budget once
  per iteration scope, not once per sibling. `wf.resolve_blocks(text)` expands
  envelopes on demand. `dedup_report(run_dir)` measures the saved re-ingestion
  from persisted per-leaf input hashes (real-world runs: 53–89% redundant input
  eliminated).

- **Budget replay on resume.** At engine init, committed leaf spend and block
  charges are rehydrated from the journal so the ceiling is enforced correctly
  across N crash-resume cycles (≤1× ceiling, not N×). WAL event `budget_replay`.

- **Verifier identity enforcement.** `verifier_policy={"must_differ_from_executor": True}`
  fails closed with `VerifierIdentityError` at loop start when the verifier
  resolves to the executor's `(provider, model)`. Never silently self-grades.
  WAL event `verifier_identity_collision`.

- **Finalize-status fix.** All leaves completed + finalization raises → status is
  `completed_with_warnings`, never a bare `failed` with `error=None`.

- **New public exports:** `GateResult`, `GateRunner`, `GoalContract`,
  `default_goal_contract`, `HandoffReport`, `FailureSignature`,
  `FailureSignatureRegistry`, `RepairRouter`.

## 1.3.0 — visual run card (2026-06-06)

- **`progress.card_html(run_id)` + `progress.render_card(run_id)`** — a self-contained dark
  HTML dashboard for a run (phases, per-leaf status/model/latency/cost/retries) and a PNG
  renderer. The renderer shells out to a Puppeteer-style `html2png.js` helper supplied via the
  `html2png=` arg or the `FLOW_HTML2PNG` env var; with no renderer it degrades to `None` and
  never raises. Same data as `trace`, but an image you can drop into a chat/dashboard.

## 1.2.0 — native Anthropic tools (8.5 → 9 follow-through, 2026-06-05)

- **First-class tools on the `anthropic_sdk` backend**, at full parity with `openai_http`:
  the Claude Messages `tool_use`/`tool_result` loop with per-leaf grants, approval-gate
  overrides, the `max_tool_iterations` cap (fails closed, never infinite), and accumulated
  token/usd accounting across turns. `to_anthropic_schema()` emits the `{name, description,
input_schema}` tool shape; unknown tools come back as an `is_error` tool_result so the model
  can recover instead of crashing. Using Claude with tools no longer requires an OpenAI-compatible
  proxy. The fail-closed gate now allows `openai_http` **or** `anthropic_sdk`; other backends
  (`shell_cmd`, `codex`) still reject tool grants with a clear error.
- **Hermetic tests** (`tests/test_tools_anthropic.py`, 5): a fake `anthropic` module injected via
  `sys.modules` — no SDK install, no network — covering execute / cap / approval-denied / unknown-tool
  recovery / schema shape. The shared test config gained an `anthropic_sdk` provider + `claude` model.

## 1.1.0 — 8+ upgrades (deep audit follow-through, 2026-06-05)

- **Cancellation + deadline cascade:** Ctrl+C/SIGTERM cancels cooperatively; a deadline breach stops the whole fleet; `wf.cancelled()`; cancelled leaves re-run on resume (they're unfinished work, not skipped).
- **Explicit failure modes:** `wf.parallel(..., mode=...)` / `wf.pipeline(..., mode=...)` — `lenient` (default, `None`+warn), `fail_fast` (raise `ParallelError`), `collect_errors` (`ExecutionResult` envelopes).
- **Inspectable resume state:** `flow status <run_id>` (+ `--json`) shows the node-state manifest and exactly what `resume` would re-run, with estimated rerun cost.
- **First-class tools:** `register_tool(ToolDefinition(...))` + `wf.agent(tools=[...], tool_approval_gates={...})` — a bounded tool-use loop in the openai_http backend with per-leaf grants and approval gates; budgeted.
- **Cross-run content cache (opt-in):** `leaf.content_cache.enabled` reuses identical leaves across runs/machines by content hash.
- **Cost prediction:** `flow run --dry-run --estimate-cost` forecasts spend per leaf with zero model calls.
- **Live + kill-resume CI:** external-SIGKILL-then-resume smoke (no creds) + credential-gated live provider tests (`pytest -m live`); fixed httpx HTTP-status classification (402/429 no longer mis-marked retryable).

## 1.0.1 — audit fixes (Albedo dogfood audits ×2, 2026-06-05)

Re-audit round (score 4.0 → 7.4):

- **Fix first-run DX break:** `flow init` now writes `config.json` (zero-dep
  readable) instead of YAML, so the default install's `init`→`self-test --offline`
  works without PyYAML. Loader reads `.json` or `.yaml`. CI now runs that exact path.
- **Offline, key-free example** (`examples/offline_local.py`) runs in CI.
- `flow selftest` accepted as an alias for `flow self-test`.
- Honest README: parallel/pipeline turn failures into `None`+warning; `trace` shows
  null provider/model for pre-backend failures; resume identity keys; a "tested vs
  not" section separating CI-proven core from credential-gated live paths.

First round:

- **Fix release blocker:** ship `constants.py` + `backends/codex.py` and all new
  files (were untracked → clean clone failed to `import flow`). Added an
  `install-smoke` CI job (build wheel → clean venv → import + CLI + self-test +
  example) so this can't recur.
- **Subscriptions / OAuth:** `codex` backend (ChatGPT Pro Responses API) + generic
  token sources (`auth_env`/`auth_file`+`auth_field`/`auth_cmd`) + custom headers —
  use subscriptions, not just API keys.
- **Real JSON Schema validation** via `jsonschema` (`flow[schema]`); documented
  top-level subset fallback when absent.
- **Leaf identity includes the output schema** — two same-prompt leaves with
  different schemas no longer collide in the resume/dedup cache.
- **Phase scoping is concurrency-safe** — context-scoped current phase; parallel/
  pipeline thunks capture the phase active at submission.
- **Structured spans:** `flow trace --json`.
- Honest docs: resume is leaf-level (not full-graph snapshot); schema needs
  `jsonschema` for full validation.

## 1.0.0

Initial public release.

- Two-tier concurrent scheduler (orchestration pool + bounded leaf pool); `parallel`/`pipeline`/`workflow`.
- Crash-resumable append-only WAL journal; `resume` skips completed leaves.
- Per-leaf cost-aware, capability-aware router with an opt-out light-model denylist.
- JSON-schema enforcement with one automatic repair turn.
- Token + USD + calls budget with deadline propagation.
- Backends: `openai_http` (stdlib default), `anthropic_sdk`, `shell_cmd` (any CLI), `local`; `register_backend()` hook.
- Single YAML/JSON config with `${VAR}` interpolation and zero-config fallback.
- `flow` CLI: `run | resume | trace | author | list | doctor | init | self-test`.
- Model-authored workflows via `flow run --nl`.
- Agent-driven setup (`AGENTS.md`).
