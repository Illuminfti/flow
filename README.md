<div align="center">

# 🍃 flow

### the dynamic-workflow engine for **any agent, any model**

_Your code owns the orchestration. Models do bounded leaf work that runs **concurrently**, routes by **cost**, enforces **JSON schemas**, retries **transient failures**, and **resumes after a crash**._

[![tests](https://github.com/Illuminfti/flow/actions/workflows/tests.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/tests.yml)
[![lint](https://github.com/Illuminfti/flow/actions/workflows/lint.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/lint.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![core deps](https://img.shields.io/badge/core%20deps-0-orange)
![backends](https://img.shields.io/badge/backends-openai%20%7C%20anthropic%20%7C%20ollama%20%7C%20any%20CLI-8a2be2)

```python
findings = wf.parallel([
    lambda lens=lens: wf.agent(f"audit {repo} for {lens} bugs", schema=BUG, tier="quality")
    for lens in ("correctness", "security", "performance")
])
```

_Three models, three lenses, one wall-clock second. Verified, budgeted, resumable._

</div>

---

## What it is

`flow` is the Claude-Code-Workflow pattern, set free: a tiny engine where **the script — written by you, or authored by a model from a one-line task — expresses fan-out, pipelines, and verification**, and the engine runs the leaves across a real concurrency pool, picks the cheapest capable model per leaf, validates structured output, retries flaky calls, and writes a crash-resumable journal.

Model-agnostic. Agent-agnostic. **Zero required dependencies.** Point Claude Code, a Hermes agent, or any tool-using LLM at it.

```mermaid
flowchart LR
    S["your script<br/>run(wf, args)"] -->|wf.parallel / pipeline / workflow| O((orchestration<br/>pool))
    O -->|leaf descriptors| Q{router<br/>cost + caps}
    Q -->|tier→cheapest capable| L((leaf<br/>pool))
    L --> B1[openai_http]
    L --> B2[anthropic]
    L --> B3[shell_cmd<br/>ollama · llm · any CLI]
    L --> B4[local]
    L -->|every transition| J[(WAL journal<br/>crash-resume)]
    J -.->|flow resume| L
    Q -.->|budget gate<br/>tokens · usd · calls| L
    L -.->|retry + backoff| B1
```

## Why it's different

|                                     |                        **flow**                         |    Claude Code Workflow    |
| ----------------------------------- | :-----------------------------------------------------: | :------------------------: |
| Runs anywhere                       |                ✅ any box, `pip install`                | ❌ inside Claude Code only |
| Any model                           | ✅ OpenAI · Anthropic · Ollama · DeepSeek · **any CLI** |       ❌ Claude only       |
| Per-leaf **cost-aware** routing     |             ✅ tiers pick cheapest capable              |        ❌ one tier         |
| Crash-resume across processes       |              ✅ fsync'd WAL, `flow resume`              |    ⚠️ same-session only    |
| Transient-error **retry + backoff** |                       ✅ built-in                       |             ❌             |
| Schema output + auto-repair         |              ✅ + budget-gated repair turn              |             ✅             |
| True concurrency, no async          |                    ✅ two-tier pools                    |        ✅ (~16 cap)        |
| Dependencies                        |                 **0** (stdlib `urllib`)                 |            n/a             |
| License                             |                   MIT, yours to fork                    |        proprietary         |

## Install

```bash
pipx install "git+https://github.com/Illuminfti/flow"
# or:  pip install "flow[yaml,anthropic] @ git+https://github.com/Illuminfti/flow"
# or:  uv tool install "git+https://github.com/Illuminfti/flow"
```

> PyPI release (`pipx install flow`) coming soon; install from git for now.

## 30-second quickstart

```bash
export OPENAI_API_KEY=sk-...      # or any OpenAI-compatible key
flow init                         # writes ~/.config/flow/config.yaml
flow self-test --offline          # proves the engine runs — no network
flow run --nl "summarize the top 3 risks of running trading bots on one server"
```

## In Python

```python
from flow import run_workflow

BUG = {"type": "object", "required": ["title", "severity"],
       "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}

def run(wf, args):
    wf.phase("review")
    findings = wf.parallel([
        (lambda lens=lens: wf.agent(f"Review {args['files']} for {lens} bugs. Worst one only.",
                                    label=f"review:{lens}", schema=BUG, tier="quality"))
        for lens in ["correctness", "security", "performance"]
    ])
    wf.phase("verify")
    return wf.parallel([
        (lambda f=f: wf.agent(f"Refute if false: {f}", label="verify", tier="cheap", required=False))
        for f in findings if f
    ])

report = run_workflow(run_fn=run, args={"files": ["app.py"]}, budget={"max_usd": 1})
print(report["final"], report["spend"])
```

## The `wf` API — nine methods, that's the whole thing

```python
wf.agent(prompt, *, label, schema, tier, model, provider, required, max_tokens, timeout)  # one model leaf
wf.local(fn, *args, **kwargs)            # a deterministic no-model leaf (real Python)
wf.parallel([thunk, ...])                # run thunks concurrently
wf.pipeline(items, *stages)              # streaming fan-out through stages
wf.workflow(child_fn, inputs)            # nested workflow, shared pool, no depth cap
wf.phase(name)                           # checkpoint
wf.log(msg, **fields) / wf.notify(msg)   # journal / escalate
wf.spend() / wf.remaining() / wf.has_headroom()   # token + usd + calls budget
```

## Models & routing

One config file (`~/.config/flow/config.yaml`) — add any model with **zero code edits**. Define `providers`, `models` (with pricing + capabilities), and `tiers`. The router filters by capability and picks **minimum cost** within the tier. A light-model denylist is on by default (opt out with `leaf.allow_light_models: true`). → [docs/config.md](docs/config.md)

## Backends

`openai_http` (default, stdlib · OpenAI/DeepSeek/Groq/Together/Mistral/OpenRouter/Ollama/LM Studio/vLLM) · `anthropic_sdk` · `shell_cmd` (drive **any** CLI — `ollama run`, `llm`, `hermes chat`) · `local`. Add your own with `register_backend()`. → [docs/backends.md](docs/backends.md)

## Crash & resume

```bash
flow run myflow.py --run-id audit-42      # crashes? kill it, then:
flow resume audit-42 myflow.py            # completed leaves are skipped, not recomputed
flow trace audit-42                        # per-leaf model / cost / latency / retries
```

## For AI agents

Setting this up _as an agent_? Read **[AGENTS.md](AGENTS.md)** — it's written for you.

## License

MIT © Illumi ([@Illuminfti](https://github.com/Illuminfti)) · contributions welcome
