# Examples

Use these as copyable workflow shapes.

| File | Demonstrates | Offline | Requires model | Command |
| --- | --- | --- | --- | --- |
| `offline_local.py` | local leaves, parallelism, no model calls | yes | no | `flow run examples/offline_local.py` |
| `quickstart.py` | minimal model workflow with review + verify phases | no | yes | `flow run examples/quickstart.py --args '{"target":"src/flow"}' --budget '{"max_usd":1,"max_calls":10}'` |
| `simple_parallel.py` | fan-out over topics | no | yes | `flow run examples/simple_parallel.py --args '{"topics":["sui","solana"]}' --budget '{"max_usd":1}'` |
| `audit_template.py` | multi-lens audit with structured findings | no | yes | `flow run examples/audit_template.py --args '{"target":"./src","lenses":["correctness","security"]}' --budget '{"max_usd":2}'` |
| `custom_backend.py` | registering a custom backend | partly | no external model needed | `python examples/custom_backend.py` |
| `patterns/classify_and_act.py` | classification routed to follow-up actions | no | yes | see file |
| `patterns/generate_and_filter.py` | generate many candidates, filter down | no | yes | see file |
| `patterns/loop_until_done.py` | iterative loop with stop condition | no | yes | see file |
| `patterns/tournament.py` | pairwise comparative judging | no | yes | see file |

Run the offline proof first:

```bash
flow self-test --offline
flow run examples/offline_local.py
```

Then configure a provider with `flow init` and `flow doctor` before running model-backed examples.
