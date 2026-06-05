<h1 align="center">🍃 flowleaf</h1>

<p align="center">
  <b>A dynamic-workflow engine for any agent, any model.</b><br>
  Code owns the orchestration; models do bounded leaf work that runs <i>concurrently</i>,
  routes by <i>cost</i>, enforces <i>JSON schemas</i>, and <i>resumes after a crash</i>.
</p>

<p align="center">
  <a href="https://github.com/Illuminfti/flowleaf/actions"><img src="https://github.com/Illuminfti/flowleaf/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/deps-stdlib%20core-orange" alt="deps">
</p>

---

`flowleaf` is the [Claude-Code-Workflow](https://docs.claude.com/) pattern as a standalone library:
your script (written by you, or authored by a model from a one-line task) expresses fan-out,
pipelines, and verification; the engine runs the leaves across a real concurrency pool, picks the
cheapest capable model per leaf, validates structured output, and writes a crash-resumable journal.

It is **model-agnostic** (any OpenAI-compatible API, Anthropic, Ollama/LM Studio, or any CLI) and
**agent-agnostic** (point Claude Code, a Hermes agent, or any tool-using LLM at it).

## Why

- **True concurrency, no async** — leaves run on a real pool; `parallel()` overlaps wall-clock.
- **Crash-resumable** — every leaf transition is an fsync'd WAL line; `flowleaf resume <run_id>` skips completed work.
- **Cost-aware routing** — per-leaf tiers (`quality`/`cheap`/`free`/`local`) pick the cheapest capable model.
- **Schema enforcement** — pass a JSON Schema; get validated output with one automatic repair turn.
- **Model-authored workflows** — `flowleaf run --nl "audit X across 4 lenses"` writes and runs the script.
- **Zero core deps** — the default backend is stdlib `urllib`. Extras add YAML/Anthropic/httpx.

## Install

```bash
pipx install "git+https://github.com/Illuminfti/flowleaf"
# or:  pip install "flowleaf[yaml,anthropic] @ git+https://github.com/Illuminfti/flowleaf"
# or:  uv tool install "git+https://github.com/Illuminfti/flowleaf"
```

> PyPI release (`pipx install flowleaf`) coming soon; install from git for now.

## 30-second quickstart

```bash
export OPENAI_API_KEY=sk-...      # or any OpenAI-compatible key
flowleaf init                     # writes ~/.config/flowleaf/config.yaml
flowleaf self-test --offline      # proves the engine runs (no network)
flowleaf run --nl "summarize the top 3 risks of running trading bots on one server"
```

In Python:

```python
from flowleaf import run_workflow

FINDING = {"type": "object", "required": ["title", "severity"],
           "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}

def run(wf, args):
    wf.phase("review")
    findings = wf.parallel([
        (lambda lens=lens: wf.agent(f"Review {args['files']} for {lens} bugs. Worst one only.",
                                    label=f"review:{lens}", schema=FINDING, tier="quality"))
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

## The Workflow API

| method                                                                            | what                                        |
| --------------------------------------------------------------------------------- | ------------------------------------------- |
| `wf.agent(prompt, *, label, schema, tier, model, provider, required, max_tokens)` | one bounded model leaf                      |
| `wf.local(fn, *args)`                                                             | a deterministic no-model leaf (real Python) |
| `wf.parallel([thunk, ...])`                                                       | run thunks concurrently                     |
| `wf.pipeline(items, *stages)`                                                     | streaming fan-out through stages            |
| `wf.workflow(child_fn, inputs)`                                                   | nested workflow, shared pool, no depth cap  |
| `wf.phase(name)` / `wf.log(msg)` / `wf.notify(msg)`                               | checkpoint / journal / escalate             |
| `wf.spend()` / `wf.remaining()` / `wf.has_headroom()`                             | token+usd+calls budget                      |

## Models & routing

Everything is config (`~/.config/flowleaf/config.yaml`) — no code edits to add a model. Define
`providers`, `models` (with pricing + capabilities), and `tiers`. The router filters by capability
and picks minimum cost within the tier. A light-model denylist is on by default; opt out with
`leaf.allow_light_models: true`. See [docs/config.md](docs/config.md).

## Backends

`openai_http` (default, stdlib), `anthropic_sdk` (`[anthropic]` extra), `shell_cmd` (drive any CLI —
`ollama run`, `llm`, `hermes chat`, …), and `local`. Add your own with `register_backend()`. See
[docs/backends.md](docs/backends.md).

## Crash & resume

```bash
flowleaf run myflow.py --run-id audit-42      # crashes? kill it, then:
flowleaf resume audit-42 myflow.py            # completed leaves are skipped
flowleaf trace audit-42                        # per-leaf model / cost / latency
```

## For AI agents

Setting this up _as an agent_? Read **[AGENTS.md](AGENTS.md)**.

## License

MIT © Illumi ([@Illuminfti](https://github.com/Illuminfti))
