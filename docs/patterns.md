# Workflow patterns

Distilled from Anthropic's _["A harness for every task: dynamic workflows in Claude
Code"](https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code)_
(Thariq Shihipar & Sid Bidasaria) and adapted to the `flow` API. Runnable
versions are in [`examples/patterns/`](../examples/patterns).

## The three failure modes workflows fight

| Failure mode               | What it is                                                   | Pattern that fights it                                |
| -------------------------- | ------------------------------------------------------------ | ----------------------------------------------------- |
| **Agentic laziness**       | the model declares a complex job done after partial progress | loop-until-done, generate-and-filter                  |
| **Self-preferential bias** | the model prefers its own output when grading it             | adversarial verification, tournament (separate judge) |
| **Goal drift**             | the original objective decays across many turns / compaction | classify-and-act (decide the route once, up front)    |

## The patterns

- **classify-and-act** — a cheap classifier decides the task type, then route to the
  right handler/tier. → [`examples/patterns/classify_and_act.py`](../examples/patterns/classify_and_act.py)
- **fan-out-and-synthesize** — split into independent steps, each with its own clean
  context, then merge. Use when steps must not cross-contaminate. → [`examples/audit_template.py`](../examples/audit_template.py)
- **adversarial verification** — for each producing agent, spawn a _separate_ agent to
  refute its output against a rubric. Default to "refuted" when unsure. → audit_template
- **generate-and-filter** — generate many candidates, dedupe, filter by a rubric/verifier,
  keep only survivors. → [`examples/patterns/generate_and_filter.py`](../examples/patterns/generate_and_filter.py)
- **tournament / comparative judgment** — N approaches compete; judges pick winners by
  **pairwise** comparison (more reliable than absolute scoring). → [`examples/patterns/tournament.py`](../examples/patterns/tournament.py)
- **loop-until-done** — for unknown-size work, loop until K dry rounds (no new results),
  bounded by budget — not a fixed count. → [`examples/patterns/loop_until_done.py`](../examples/patterns/loop_until_done.py)

## Verification (deeper)

- **deep verification** — one agent extracts factual claims; a subagent checks _each_
  claim; another rates source quality.
- **multi-rule** — one verifier agent _per rule_; a "skeptic" agent reviews the rules
  themselves to keep false-positives down.

## When to use — and when not to

**Use** for long-running massively-parallel work, structured problems needing
verification, adversarial/independent-hypothesis tasks, large migrations, deep
research, qualitative sorting at 1000+ scale, root-cause investigation.

**Don't** for ordinary tasks. Ask: _does it really need more compute?_ Workflows use
**significantly more tokens** — most coding tasks do not need a panel of 5 reviewers.
Always set a budget (`--budget '{"max_usd": 2}'` or `max_tokens`); `flow` enforces it
as a hard ceiling and the router picks the cheapest capable model per leaf.

## Security: quarantine

Leaves that read untrusted public content must not take high-privilege actions. `flow`'s
authored-script allowlist (`authoring.validate`) is a guardrail against the _model_, not a
sandbox against an adversary — run untrusted scripts with `executor_kind="process"` + OS
isolation.
