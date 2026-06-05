# Backends

A backend is a callable `prompt -> BackendResponse`. The engine (scheduler, journal, budget,
router, schema repair) is backend-agnostic, so adding a model provider = adding a backend.

## Built-in

| kind            | use                                                                                                                                                                                  | config                 |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- |
| `openai_http`   | **default**. Any OpenAI-compatible `/chat/completions` (OpenAI, DeepSeek, Groq, Together, Mistral, OpenRouter, Ollama, LM Studio, vLLM). Stdlib `urllib`; uses `httpx` if installed. | `base_url`, `auth_env` |
| `anthropic_sdk` | Anthropic Messages API. Needs `[anthropic]`. **Native first-class tools** (`tool_use`/`tool_result` loop, per-leaf grants + approval gates + iteration cap), same as `openai_http`.  | `auth_env`             |
| `shell_cmd`     | Drive any CLI. `cmd_template` is an argv **list** with `{prompt}`/`{model}`/`{provider}`/`{toolsets}` placeholders, run with `shell=False` (no injection).                           | `cmd_template`         |
| `local`         | A deterministic Python callable (`wf.local`). No model.                                                                                                                              | —                      |

Examples of `shell_cmd` templates:

```yaml
cmd_template: ["ollama","run","{model}","{prompt}"]
cmd_template: ["llm","-m","{model}","{prompt}"]
cmd_template: ["hermes","chat","--provider","openai-codex","-m","{model}","--ignore-user-config","-Q","-q","{prompt}"]
```

## Custom backends

```python
from flow import register_backend
from flow.backends.base import BackendResponse

def build(req, cfg, provider_cfg):
    def backend(prompt, *, timeout=None):
        text = my_model_call(prompt, model=req.route.model)   # your SDK / in-process agent
        return BackendResponse(text=text, input_tokens=..., output_tokens=...,
                               provider=req.route.provider, model=req.route.model)
    return backend

register_backend("my_kind", build)
```

Then point a provider at it: `providers: {mine: {kind: my_kind}}` and add a model under it. This is how
a host agent (e.g. Hermes, Claude Code) exposes its own in-process model loop as a flow backend.

## BackendResponse

```python
BackendResponse(text, input_tokens, output_tokens, usd=0.0, tokens_estimated=False,
                provider="", model="", native=None)
```

Raise `flow.BackendError` for failures — the scheduler turns it into a failed leaf, never a crash.
