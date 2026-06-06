# flow — setup guide for AI agents

You are an AI agent reading this repo to install and wire **flow** as a tool for
yourself or your user. Follow these steps in order. Each is copy-pasteable.

## 1. Install

```bash
pipx install "git+https://github.com/Illuminfti/flow"          # preferred (isolated)
# or
pip install "flow[yaml,anthropic] @ git+https://github.com/Illuminfti/flow"
# or
uv tool install "git+https://github.com/Illuminfti/flow"
```

(PyPI release coming soon; install from git for now.)

## 2. Configure

Zero-config path (works immediately if an OpenAI-compatible key is present):

```bash
export OPENAI_API_KEY=sk-...     # or DEEPSEEK_API_KEY, or run Ollama locally
flow init                    # writes ~/.config/flow/config.yaml, lists detected keys
```

To add Anthropic, Ollama, DeepSeek, or any OpenAI-compatible endpoint, edit that file.
The schema is in `docs/config.md`. Adding a model is pure config — no code.

## 3. Verify (do this before using it)

```bash
flow self-test --offline     # imports + config + local-only DAG smoke. Exit 0 = green. NO network.
flow self-test --online      # one real model call. Needs a configured key.
```

If `--offline` is green, the local install and core engine smoke path work. `--online` verifies configured provider connectivity with one real model call, not full production readiness or every provider/model.

## 4. Use

```bash
flow run path/to/workflow.py --args '{"files":["a.py","b.py"]}'
flow run --nl "audit these files across 3 lenses and verify each finding" --args '{"files":["a.py"]}'
flow trace <run_id>          # per-leaf model / cost / latency
flow resume <run_id> path/to/workflow.py   # after a crash
```

A workflow script defines `run(wf, args)`; the API is in the README and `examples/`. Pipeline stages may be `stage(cur)`, `stage(cur, item)`, `stage(cur, item, idx)`, or `stage(*args)`; `cur` is the current value, `item` is the original input, and `idx` is the zero-based input index. JSON Schema dicts work without extras using a limited fallback validator when `jsonschema` is absent; install the full extras for complete JSON Schema enforcement. Pydantic model schemas require `pydantic`.

## 5. Register flow as a tool for your host agent

- **Claude Code** — allow the `flow` Bash prefix in `~/.claude/settings.json`, or wrap it as a
  skill that shells out to `flow run`. You can now call multi-agent workflows from a single tool.
- **Hermes** — `flow` is a CLI; register a shell tool that runs it. For in-process leaves, use
  `flow.register_backend("inproc", builder)` to call your agent's own model loop (see docs/backends.md).
- **Any tool-using LLM** — expose `flow run --nl "<task>" --args '<json>'` as a tool. The model
  describes the task; flow authors + runs the concurrent, verified workflow and returns the result.

## ✓ Done

```bash
flow doctor                  # config + provider key diagnostics (never prints secrets)
flow list                    # past runs
```
