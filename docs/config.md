# Configuration

flowleaf reads one config file so it works with **any models, zero code edits**.

## Where it looks (first match wins)

1. `$FLOWLEAF_CONFIG`
2. `./.flowleaf.yaml`
3. `$XDG_CONFIG_HOME/flowleaf/config.yaml` (default `~/.config/flowleaf/config.yaml`)
4. **zero-config** — if `OPENAI_API_KEY` is set, a one-model `quality` setup is synthesised.

`flowleaf init` writes a starter file. YAML needs the `[yaml]` extra; `.json` works with no extras.

## Schema

```yaml
engine:
  executor: thread # thread | process
  max_workers: null # null -> min(32, 4*cpu)
  orch_workers: null # null -> 256
  data_dir: null # null -> $XDG_DATA_HOME/flowleaf
  budget:
    { max_tokens: null, max_usd: null, max_calls: 1000, deadline_seconds: null }

leaf:
  allow_light_models: false # set true to permit -mini/-flash/-haiku/-nano/-lite
  denylist: "(?:^|[-_/])(mini|flash|haiku|nano|lite)(?:$|[-_/0-9.])"
  noise_patterns: [] # regex lines to strip from shell_cmd stdout

providers:
  openai:
    {
      kind: openai_http,
      base_url: "https://api.openai.com/v1",
      auth_env: OPENAI_API_KEY,
    }
  anthropic: { kind: anthropic_sdk, auth_env: ANTHROPIC_API_KEY }
  deepseek:
    {
      kind: openai_http,
      base_url: "https://api.deepseek.com",
      auth_env: DEEPSEEK_API_KEY,
    }
  ollama:
    {
      kind: openai_http,
      base_url: "${OLLAMA_HOST:-http://localhost:11434}/v1",
      auth_env: null,
    }
  my-cli:
    { kind: shell_cmd, cmd_template: ["llm", "-m", "{model}", "{prompt}"] }

models:
  gpt-4o:
    {
      provider: openai,
      id: gpt-4o,
      in: 2.5,
      out: 10.0,
      free: false,
      caps: [reasoning, tool_call, structured, vision],
    }
  claude-opus:
    {
      provider: anthropic,
      id: claude-opus-4-6,
      in: 5.0,
      out: 25.0,
      free: false,
      caps: [reasoning, tool_call, structured, vision],
    }
  deepseek-v3:
    {
      provider: deepseek,
      id: deepseek-chat,
      in: 0.27,
      out: 1.10,
      free: false,
      caps: [reasoning, tool_call, structured],
    }
  llama3-local:
    {
      provider: ollama,
      id: llama3,
      in: 0.0,
      out: 0.0,
      free: true,
      caps: [tool_call],
    }

tiers:
  quality: [gpt-4o, claude-opus]
  cheap: [deepseek-v3, gpt-4o]
  free: [llama3-local]
  local: []

defaults:
  tier: quality
```

## Notes

- `${VAR}` and `${VAR:-default}` interpolate environment variables (one level). Keep secrets in env,
  reference them via `auth_env` — flowleaf never stores or prints key values.
- The router filters a tier's models by required `caps` (a `schema=` leaf needs `structured`; vision
  needs `vision`) and picks the **minimum estimated cost**.
- Pin per leaf: `wf.agent(..., model="deepseek-v3")` or `provider="..."`. Pins are still denylist-checked.
- `pricing` is `in`/`out` USD per million tokens, used for cost ordering + `wf.spend()`.
