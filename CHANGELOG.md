# Changelog

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
