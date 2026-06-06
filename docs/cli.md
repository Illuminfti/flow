# CLI reference

## Global

```bash
flow --version
flow --help
```

## Run

```bash
flow run workflow.py \
  --args '{"target":"src"}' \
  --budget '{"max_usd":1,"max_calls":8,"max_tokens":50000}' \
  --run-id release-audit \
  --executor thread \
  --max-workers 8
```

Natural-language authoring plus execution:

```bash
flow run --nl "audit this repo across correctness/security/performance" \
  --budget '{"max_usd":1,"max_calls":8}'
```

Dry-run authoring without model execution:

```bash
flow run --nl "audit this repo" --dry-run --estimate-cost
```

## Resume

```bash
flow resume <run_id> workflow.py
```

## Trace

```bash
flow trace <run_id>
flow trace <run_id> --json
```

## Status

```bash
flow status <run_id>
flow status <run_id> --json
```

Shows node states and what resume would rerun.

## Author

```bash
flow author "classify these files and summarize the risky ones" -o workflow.py
```

Writes a validated workflow script without running it.

## List

```bash
flow list
```

## Doctor

```bash
flow doctor
```

Prints environment, config path, data root, and provider diagnostics. It must never print secret values.

## Init

```bash
flow init
flow init --force
```

Writes a starter config. JSON is used when YAML support is unavailable. YAML is used when PyYAML is installed.

## Self-test

```bash
flow self-test --offline
flow self-test --online
flow self-test --json
```

`--offline` performs import, config, data-root, and local DAG checks with no network. `--online` adds one real model call through the configured router.
