# Testing and release checks

## Local fast path

```bash
python -m pytest
flow self-test --offline
flow run examples/offline_local.py
python -m compileall -q src/flow
```

## Clean install smoke

```bash
python -m venv /tmp/flow-smoke
/tmp/flow-smoke/bin/pip install -e .
/tmp/flow-smoke/bin/python -c "import flow; print(flow.__version__)"
/tmp/flow-smoke/bin/flow --help
/tmp/flow-smoke/bin/flow self-test --offline
```

## Development extras

```bash
python -m pip install -e '.[dev,yaml,schema,anthropic]'
python -m pytest
ruff check .
```

## Live provider tests

Live tests are credential-gated and skipped without secrets.

```bash
OPENAI_API_KEY=*** python -m pytest -m live
ANTHROPIC_API_KEY=*** python -m pytest -m live
```

Subscription-backed routes such as Codex depend on local authenticated configuration and should be reported as environment-specific checks.

## What tests prove

CI and local tests cover:

- workflow API and scheduler behavior
- concurrency and failure modes
- budgets, deadlines, retries, and cancellation
- schema validation and repair
- leaf-level resume and journal persistence
- install smoke from a clean environment
- local and shell backends
- OpenAI-compatible and Anthropic tool loops with hermetic fakes
- trace, status, cost prediction, cache, and run-card generation

They do not prove every remote provider is currently reachable. Provider reachability is an environment and credential check, verified with `flow doctor`, `flow self-test --online`, and live tests.

## Markdown and docs hygiene

```bash
git diff --check
python -m compileall -q src/flow
```

If Node is available, optional link check:

```bash
npx markdown-link-check README.md docs/*.md
```
