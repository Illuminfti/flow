<div align="center">

# flow

### Dynamic workflows for LLM agents

**Write orchestration in Python. Let models do bounded leaf work concurrently, with budgets, routing, schemas, retries, and leaf-level crash resume.**

[![tests](https://github.com/Illuminfti/flow/actions/workflows/tests.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/tests.yml)
[![install smoke](https://github.com/Illuminfti/flow/actions/workflows/install-smoke.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/install-smoke.yml)
[![lint](https://github.com/Illuminfti/flow/actions/workflows/lint.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/lint.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![typed](https://img.shields.io/badge/typing-typed-blueviolet)
![core deps](https://img.shields.io/badge/core%20runtime%20deps-0-orange)

</div>

---

## The promise

`flow` is a small Python engine for dynamic workflows: your script owns the DAG, and model calls are treated as bounded, journaled leaves.

Use it when one prompt is not enough:

- fan out independent review lenses
- verify findings adversarially
- classify or transform batches
- run generate, judge, repair loops
- route each leaf to the cheapest capable model
- resume after a crash without recomputing completed work

Skip it for ordinary single-shot prompts, deterministic ETL, or unbudgeted low-value work. Workflows are powerful because they multiply work. Set a budget.

## Proof strip

- **Concurrent leaves:** `wf.parallel`, `wf.pipeline`, nested `wf.workflow`
- **Cost-aware routing:** model tiers pick the cheapest capable configured route
- **Structured output:** JSON Schema validation with a repair turn
- **Budgets:** tokens, USD, calls, and deadlines
- **Durability:** fsync-backed journal plus `flow resume`
- **Backends:** OpenAI-compatible HTTP, Anthropic SDK, Codex, shell commands, local Python, and custom backends
- **Core runtime deps:** zero third-party packages required

## Install

```bash
pipx install "git+https://github.com/Illuminfti/flow"
# or
pip install "flow[yaml,schema,anthropic] @ git+https://github.com/Illuminfti/flow"
# or
uv tool install "git+https://github.com/Illuminfti/flow"
```

PyPI release is pending. Install from GitHub for now.

Extras:

- `flow[yaml]`: YAML config support
- `flow[schema]`: full JSON Schema validation through `jsonschema`
- `flow[anthropic]`: native Anthropic SDK backend
- `flow[all]`: all optional runtime extras

## 30-second quickstart

No key, no network:

```bash
flow self-test --offline
flow run examples/offline_local.py
```

Expected shape:

```text
offline: PASS (4 leaves; providers=[...])
{
  "status": "completed",
  "leaf_count": 4,
  "failed_count": 0
}
```

First real model run:

```bash
export OPENAI_API_KEY=sk-...
flow init
flow doctor
flow run --nl "review this repo for the three highest-risk release blockers" \
  --budget '{"max_usd":1,"max_calls":8,"max_tokens":50000}'
```

Inspect it:

```bash
flow list
flow trace <run_id>
flow status <run_id>
```

## Minimal Python workflow

Save this as `examples/quickstart.py`:

```python
from flow import run_workflow

FINDING = {
    "type": "object",
    "required": ["title", "severity"],
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string"},
    },
}


def run(wf, args):
    wf.phase("review")
    lenses = ["correctness", "security", "performance"]
    findings = wf.parallel([
        lambda lens=lens: wf.agent(
            f"Review {args['target']} for one {lens} issue. Return JSON only.",
            label=f"review:{lens}",
            schema=FINDING,
            tier="quality",
            max_tokens=600,
        )
        for lens in lenses
    ])

    wf.phase("verify")
    return wf.parallel([
        lambda finding=finding: wf.agent(
            f"Try to refute this finding. If real, explain why: {finding}",
            label="verify",
            tier="cheap",
            required=False,
            max_tokens=500,
        )
        for finding in findings
        if finding
    ])


if __name__ == "__main__":
    report = run_workflow(
        run_fn=run,
        args={"target": "src/"},
        budget={"max_usd": 1, "max_calls": 10},
    )
    print(report["final"])
```

Run it:

```bash
python examples/quickstart.py
```

## Mental model

```mermaid
flowchart LR
    A[Python workflow<br/>run(wf, args)] --> B[orchestration pool]
    B --> C[leaf requests]
    C --> D{router<br/>tier + capability + cost}
    D --> E[leaf pool]
    E --> F[OpenAI-compatible HTTP]
    E --> G[Anthropic SDK]
    E --> H[Codex or any CLI]
    E --> I[local Python]
    E --> J[(journal)]
    J --> K[resume skips completed leaves]
```

The workflow code is deterministic control flow. Models do only the bounded leaf work you assign them.

## The `wf` API

```python
wf.agent(prompt, *, label=None, schema=None, tier=None, model=None,
         provider=None, backend=None, max_tokens=None, timeout=None,
         tools=None, tool_approval_gates=None, required=True)

wf.local(fn, *args, label=None, schema=None, **kwargs)
wf.parallel([thunk, ...], mode="lenient")
wf.pipeline(items, stage1, stage2, ..., mode="lenient")
wf.workflow(child_fn, inputs=None, label="nested")
wf.phase(name)
wf.log(message, **fields)
wf.notify(message, **fields)
wf.spend()
wf.remaining()
wf.has_headroom(min_tokens=1000)
wf.cancelled()
```

Failure modes for `parallel` and `pipeline`:

- `lenient`: failed item becomes `None` and logs a warning
- `fail_fast`: first completed failure raises `ParallelError` and cancels pending siblings
- `collect_errors`: returns `ExecutionResult` envelopes in input order

Pipeline stages may be `stage(cur)`, `stage(cur, item)`, `stage(cur, item, idx)`, or `stage(*args)`.

## CLI reference

```bash
flow run workflow.py --args '{"target":"src"}' --budget '{"max_usd":1}'
flow run --nl "audit this repo across four lenses" --budget '{"max_calls":8}'
flow run --nl "draft a workflow" --dry-run --estimate-cost
flow resume <run_id> workflow.py
flow trace <run_id> --json
flow status <run_id> --json
flow author "make a workflow that classifies these files" -o workflow.py
flow doctor
flow self-test --offline
flow self-test --online
```

Full reference: [`docs/cli.md`](docs/cli.md).

## Backends and routing

Configuration lives at `~/.config/flow/config.json` or `~/.config/flow/config.yaml`, unless `FLOW_CONFIG` points elsewhere. `flow init` writes JSON by default when YAML support is not installed, and YAML when PyYAML is available.

| Backend | Use case | Tools | Extra dependency |
| --- | --- | --- | --- |
| `openai_http` | OpenAI, DeepSeek, OpenRouter, Groq, Together, Ollama-compatible servers | yes | none |
| `anthropic_sdk` | Claude via Anthropic SDK | yes | `flow[anthropic]` |
| `codex` | ChatGPT subscription-backed Codex route where configured | no, fails closed | none |
| `shell_cmd` | any authenticated CLI, local model CLI, custom bridge | no, fails closed | none |
| `local` | deterministic Python functions | n/a | none |

More: [`docs/config.md`](docs/config.md), [`docs/backends.md`](docs/backends.md), [`docs/subscriptions.md`](docs/subscriptions.md).

## Crash resume and observability

Every run writes a journal under the configured data directory. Completed leaves are skipped on resume if their identity matches the script, phase, label, prompt, route, schema, and behavior-affecting options.

```bash
flow run myflow.py --run-id release-audit
# kill it, reboot, or lose the terminal
flow resume release-audit myflow.py
flow trace release-audit
flow status release-audit
```

`flow trace` shows per-leaf status, model, latency, retries, and spend. Leaves that fail before a backend call can show `provider/model: null` by design.

## Patterns

Runnable examples live in [`examples/`](examples) and [`examples/patterns/`](examples/patterns). Use them as templates:

- classify and act
- generate and filter
- loop until done
- tournament judgment
- audit template
- simple parallel fan-out

Pattern guide: [`docs/patterns.md`](docs/patterns.md).

## What is tested

CI and local tests cover the engine surface that should be trusted for routine use:

- concurrency and failure modes
- budgets, deadlines, retries, and cancellation
- schema validation and repair
- leaf-level crash resume
- install smoke from a clean environment
- local and shell backends
- OpenAI-compatible and Anthropic tool loops through hermetic tests
- status, trace, cache, cost prediction, and visual run-card generation

Credential-gated tests cover live provider routes when secrets are available. Subscription-backed routes such as Codex depend on local authenticated configuration and are treated as environment-specific.

Run the same checks:

```bash
python -m pytest
flow self-test --offline
flow run examples/offline_local.py
```

Details: [`docs/testing.md`](docs/testing.md).

## Security model

Authored workflows are trusted Python, not a sandbox. `flow` validates model-authored scripts to catch unsafe accidental output, but it is not a containment boundary for hostile code.

Read before running untrusted workflows: [`SECURITY.md`](SECURITY.md) and [`docs/security.md`](docs/security.md).

## Docs

- [`docs/index.md`](docs/index.md): documentation map
- [`docs/architecture.md`](docs/architecture.md): engine internals
- [`docs/cli.md`](docs/cli.md): CLI reference
- [`docs/api.md`](docs/api.md): Python API reference
- [`docs/config.md`](docs/config.md): model and provider config
- [`docs/backends.md`](docs/backends.md): backend details
- [`docs/testing.md`](docs/testing.md): verification commands
- [`docs/troubleshooting.md`](docs/troubleshooting.md): common failures
- [`docs/comparison.md`](docs/comparison.md): positioning and tradeoffs

## Contributing

Contributions are welcome. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md). Keep new behavior covered by deterministic tests, and separate CI-proven claims from live or environment-specific claims.

## License

MIT © Illumi [@Illuminfti](https://github.com/Illuminfti)
