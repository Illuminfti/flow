# Flow Patterns

Runnable pattern examples for common workflow shapes.

| Pattern | File | Use when |
| --- | --- | --- |
| Classify and act | `classify_and_act.py` | each item needs a category-specific follow-up |
| Generate and filter | `generate_and_filter.py` | you want many candidates, then a quality gate |
| Loop until done | `loop_until_done.py` | the workflow should continue until a stop condition is met |
| Tournament | `tournament.py` | candidates need comparative judging |

Additional patterns are demonstrated in `../audit_template.py` and described in `../../docs/patterns.md`.

Run pattern files with an explicit budget when they use model leaves.
