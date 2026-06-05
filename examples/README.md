# Examples

```bash
flow run examples/simple_parallel.py --args '{"topics":["sui","solana"]}'
flow run examples/audit_template.py --args '{"target":"./src","lenses":["correctness","security"]}' --budget '{"max_usd":2}'
python examples/custom_backend.py        # register a custom backend
```

All require a configured model (`flow init` + an API key), except the engine itself which you can
exercise offline with `flow self-test --offline`.
