# Changelog

## 1.0.1 — audit fixes (Albedo dogfood audit, 2026-06-05)

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
