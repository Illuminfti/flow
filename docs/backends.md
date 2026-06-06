# Backends

A backend is a callable `prompt -> BackendResponse`. The engine (scheduler, journal, budget,
router, schema repair) is backend-agnostic, so adding a model provider = adding a backend.

## Backend matrix

| kind | use | tools | extra dependency |
| --- | --- | --- | --- |
| `openai_http` | **default**. Any OpenAI-compatible `/chat/completions` endpoint: OpenAI, DeepSeek, Groq, Together, Mistral, OpenRouter, Ollama, LM Studio, vLLM | yes | none |
| `anthropic_sdk` | Anthropic Messages API | yes | `flow[anthropic]` |
| `codex` | subscription-backed Codex route when locally configured | no, fails closed | none |
| `shell_cmd` | drive any CLI through an argv list with `shell=False` | no, fails closed | none |
| `local` | deterministic Python callable through `wf.local` | n/a | none |

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
