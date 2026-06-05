# Changelog

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
