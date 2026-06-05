# Subscriptions & OAuth — use what you already pay for

flow is **not API-key-only**. You can drive it with the OAuth / subscription
products you already have — ChatGPT Pro/Plus (Codex), Claude Max, xAI/Grok,
Gemini, etc. — at no per-token cost. Three ways, simplest first.

## 1. Drive your authenticated CLI (`shell_cmd`) — simplest, works today

Any CLI that owns its own login already handles OAuth + refresh. Point a
`shell_cmd` provider at it:

```yaml
providers:
  chatgpt: { kind: shell_cmd, cmd_template: ["codex", "exec", "{prompt}"] }
  claude: { kind: shell_cmd, cmd_template: ["claude", "-p", "{prompt}"] } # Claude Max via the official CLI
  grok: { kind: shell_cmd, cmd_template: ["grok", "{prompt}"] }
  hermes:
    {
      kind: shell_cmd,
      cmd_template:
        [
          "hermes",
          "chat",
          "--provider",
          "openai-codex",
          "-m",
          "{model}",
          "--ignore-user-config",
          "-Q",
          "-q",
          "{prompt}",
        ],
    }
models:
  claude-max:
    {
      provider: claude,
      id: claude,
      in: 0,
      out: 0,
      free: true,
      caps: [reasoning, tool_call, structured],
    }
tiers: { quality: [claude-max] }
```

This is the most robust and ToS-clean path: you're using each vendor's own app
for ordinary use; flow just orchestrates them.

## 2. ChatGPT Pro/Plus directly — the `codex` backend (verified)

Calls the Codex Responses endpoint with your ChatGPT OAuth token. Zero extra cost
on a ChatGPT Pro/Plus plan.

```yaml
providers:
  codex:
    {
      kind: codex,
      auth_file: "~/.codex/auth.json",
      auth_field: "tokens.access_token",
      account_field: "tokens.account_id",
    }
models:
  gpt55:
    {
      provider: codex,
      id: gpt-5.5,
      in: 0,
      out: 0,
      free: true,
      caps: [reasoning, tool_call, structured],
    }
tiers: { quality: [gpt55], cheap: [gpt55] }
defaults: { tier: quality }
```

(`~/.hermes/auth.json` → `providers.openai-codex.tokens.access_token` also works.)

## 3. Any OAuth HTTP endpoint — generic token sources

Every backend resolves its bearer token from one of three sources, so an OAuth
token from a file or command works anywhere an API key would:

| field                                              | meaning                                                 |
| -------------------------------------------------- | ------------------------------------------------------- |
| `auth_env: NAME`                                   | read the token from env var `NAME`                      |
| `auth_file: path` + `auth_field: a.b[0].c`         | read a JSON file, dig a dotted/indexed path             |
| `auth_cmd: "..."` (string→shell) or `["argv",...]` | run a command, use its stdout                           |
| `headers: {K: V}`                                  | extra request headers (supports `${VAR}` interpolation) |

Example — an OpenAI-compatible endpoint behind an OAuth token + vendor headers:

```yaml
providers:
  vendor:
    {
      kind: openai_http,
      base_url: "https://api.vendor.com/v1",
      auth_cmd: "vendor-cli print-token",
      headers: { X-Title: "flow", HTTP-Referer: "https://example.com" },
    }
```

## Notes

- Subscription OAuth is intended for ordinary use of each vendor's own apps. Routing
  third-party traffic through subscription credentials may be against a vendor's
  terms (Anthropic restricts it explicitly) — that's your call. The `shell_cmd` →
  official-CLI path (option 1) sidesteps this by using the vendor's own client.
- Tokens are read at call time and never written to the journal, trace, or card.
- Token **refresh** is the owning app's job — keep its CLI/login current (e.g.
  `codex login`, `claude auth login`).
