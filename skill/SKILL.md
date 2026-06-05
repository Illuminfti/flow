---
name: flow
description: Run dynamic multi-agent workflows with the `flow` engine — concurrent leaves, per-leaf cost-aware model routing (any provider, API key OR subscription/OAuth), JSON-schema-enforced output, transient-error retry, token/usd budgets, and crash-resumable runs. Use when a task wants fan-out + verification, deep research, large audits/migrations, judge panels, or qualitative sorting at scale — anything that benefits from concurrent, verified sub-agents instead of one linear context.
version: 1.0.1
tags:
  [
    flow,
    workflow,
    orchestration,
    multi-agent,
    fan-out,
    verification,
    llm,
    any-model,
  ]
triggers:
  - user wants a dynamic / multi-agent workflow run on any box
  - task benefits from concurrent verified sub-agents (audit, deep research, judge panel, migration, large-scale sort)
  - user mentions the `flow` tool or github.com/Illuminfti/flow
  - need cost-aware model routing across providers or via a subscription (ChatGPT Pro / Claude Max / Grok)
---

# flow — dynamic workflows for any agent, any model

`flow` is a tiny Python engine where **your code owns the orchestration** and models
do bounded leaf work that runs concurrently, routes by cost, enforces JSON schemas,
retries transient failures, and resumes after a crash. Repo:
https://github.com/Illuminfti/flow

## Install (once)

```bash
pipx install "git+https://github.com/Illuminfti/flow"   # or: uv tool install / pip install
flow init                                                # writes ~/.config/flow/config.yaml
flow self-test --offline                                 # proves the engine runs, no network
```

Set a key (`export OPENAI_API_KEY=...`) **or** use a subscription you already have
(ChatGPT Pro/Claude Max/Grok) — see `docs/subscriptions.md`. Then `flow self-test --online`.

## Use it two ways

**A. Natural language** (the model authors + runs the workflow):

```bash
flow run --nl "audit ./src across correctness/security/perf and verify each finding" \
  --args '{}' --budget '{"max_usd":2}'
```

**B. A script** defining `run(wf, args)`:

```python
BUG = {"type": "object", "required": ["title", "severity"],
       "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}

def run(wf, args):
    wf.phase("review")
    findings = wf.parallel([
        (lambda lens=lens: wf.agent(f"Review {args['files']} for {lens} bugs. Worst one only.",
                                    label=f"review:{lens}", schema=BUG, tier="quality"))
        for lens in ["correctness", "security", "performance"]])
    wf.phase("verify")
    return wf.parallel([
        (lambda f=f: wf.agent(f"Refute if false: {f}", label="verify", tier="cheap", required=False))
        for f in findings if f])
```

```bash
flow run myflow.py --args '{"files":["app.py"]}' --budget '{"max_usd":1}'
flow trace <run_id>            # box-drawing dashboard (model/cost/latency/retries)
flow trace <run_id> --json     # structured spans
flow resume <run_id> myflow.py # after a crash — completed leaves are skipped
```

## The `wf` API (the whole surface)

`wf.agent(prompt, *, label, schema, tier, model, provider, required, max_tokens, timeout)` ·
`wf.local(fn, *args)` · `wf.parallel([thunk,...])` · `wf.pipeline(items, *stages)` ·
`wf.workflow(child_fn, inputs)` · `wf.phase(name)` · `wf.log/notify` ·
`wf.spend()/remaining()/has_headroom()`. Build a list of zero-arg lambdas for
`parallel`; bind loop vars with defaults (`lambda d=d: ...`).

## Patterns (when to reach for flow)

classify-and-act · fan-out-and-synthesize · adversarial verification ·
generate-and-filter · tournament (pairwise judgment) · loop-until-done. Each fights
a failure mode (agentic laziness / self-preferential bias / goal drift). Runnable
examples in `examples/patterns/`; details in `docs/patterns.md`.

**Don't** use a workflow for ordinary one-shot tasks — they cost significantly more
tokens. Always pass a `--budget`.

## Doctrine for the agent using this skill

1. Confirm install: `flow doctor` (lists providers + which keys are present).
2. Prefer `--nl` for ad-hoc tasks; write a script for repeatable ones (save under
   the project or `~/.config/flow/`).
3. Pick a `tier` per leaf: `quality` for hard work, `cheap`/`free` for verification.
4. Always set a budget; inspect `flow trace <run_id>` after.
5. For a real run, verify against the trace (routed model + cost per leaf), not the plan.

Full agent setup guide: `AGENTS.md` in the repo.
