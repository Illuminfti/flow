# Contributing

Thank you for improving `flow`.

## Local setup

```bash
git clone https://github.com/Illuminfti/flow
cd flow
python -m pip install -e '.[dev,yaml,schema,anthropic]'
```

## Verify before opening a PR

```bash
python -m pytest
flow self-test --offline
flow run examples/offline_local.py
python -m compileall -q src/flow
git diff --check
```

If you change docs, check README and docs links where possible.

## Contribution standards

- Add deterministic tests for behavior changes.
- Keep live-provider tests behind the `live` marker.
- Do not print secrets in diagnostics, tests, logs, or docs.
- Separate CI-proven claims from credential-gated or ad-hoc checks.
- Prefer small, inspectable workflow examples over magical demos.

## Commit style

Use short conventional commits when practical:

```text
fix: repair workflow authoring validation failures
docs: add CLI reference
feat: add backend health check
```
