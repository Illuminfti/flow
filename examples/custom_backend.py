"""Register a custom backend (e.g. your agent's own in-process model loop).

    python examples/custom_backend.py
"""
from flowleaf import register_backend, run_workflow
from flowleaf.backends.base import BackendResponse


def _build_my_backend(req, cfg, provider_cfg):
    def backend(prompt, *, timeout=None):
        # Replace with a real call into your host agent / SDK.
        text = f"[echo:{req.route.model}] {prompt[:40]}"
        return BackendResponse(text=text, input_tokens=len(prompt) // 4,
                               output_tokens=len(text) // 4, provider=req.route.provider,
                               model=req.route.model)
    return backend


register_backend("my_backend", _build_my_backend)

# Then in config: a provider with kind: my_backend, and a model pointing at it.
# Here we just prove the hook is wired:
if __name__ == "__main__":
    print("registered custom backend 'my_backend'")
