# Changelog

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
