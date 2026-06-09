# Documentation

Start here when you need more than the README.

## Core guides

- [Agentic leaves](agents.md): `wf.code`, agent config (harness/sandbox/allowed_tools/reserve_tokens), worktree isolation, session continuation, schema repair, budget floor, transcripts. **Start here for v3.**
- [Architecture](architecture.md): scheduler, leaf pool, worktree layer, journal, routing, resume, v2 loop/block, v3 agent_cli backend.
- [Loop envelope](loop.md): `wf.loop` full guide — spec fields, stop precedence, gates, goal contracts, stall detection, repair routing, handoff reports, crash-resume, verifier identity.
- [CLI reference](cli.md): every command and the important flags.
- [Python API](api.md): `run_workflow`, `wf.*`, tools, backends, errors, and v2/v3 exports.
- [Configuration](config.md): providers, models, tiers, auth, budgets, config lookup.
- [Backends](backends.md): built-in backends and custom backend registration.
- [Subscriptions](subscriptions.md): OAuth and subscription-backed routes.
- [Patterns](patterns.md): reusable workflow shapes and when to use them.

## Operating guides

- [Testing](testing.md): local, CI, install-smoke, live-test, and release checks.
- [Security](security.md): trust boundaries, authored workflows, shell commands, secrets.
- [Troubleshooting](troubleshooting.md): common setup and runtime failures.
- [Comparison](comparison.md): tradeoffs against embedded agent workflow systems.

## Examples

- [`examples/offline_local.py`](../examples/offline_local.py): no-key local proof.
- [`examples/quickstart.py`](../examples/quickstart.py): minimal online model workflow.
- [`examples/audit_template.py`](../examples/audit_template.py): multi-lens audit shape.
- [`examples/patterns/`](../examples/patterns): reusable pattern implementations.
