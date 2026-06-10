---
name: flow
description: Run dynamic multi-agent workflows with the `flow` engine — concurrent leaves, per-leaf cost-aware model routing (any provider, API key OR subscription/OAuth), JSON-schema-enforced output, transient-error retry, token/usd budgets, crash-resumable runs, bounded iterate-to-goal loops with acceptance gates + an independent verifier, shared-context block dedup so wide fan-out doesn't breach token budgets, (v3) full CLI coding agents as leaves via wf.code — Codex ($0 via ChatGPT Pro), Claude Code, or any harness — running in isolated checkouts with patch evidence, and (v4) wf.merge: drive a fan-out of agent patches to merged, proof-gated, auto-reverting code on a branch behind a fail-closed allowlist money fence. Use when a task wants fan-out + verification, deep research, large audits/migrations, judge panels, iterate-until-verified repair loops, qualitative sorting at scale, parallel agentic code generation, or autonomously shipping a backlog — anything that benefits from concurrent, verified sub-agents instead of one linear context.
version: 4.0.0
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
    coding-agents,
    worktree,
  ]
triggers:
  - user wants a dynamic / multi-agent workflow run on any box
  - task benefits from concurrent verified sub-agents (audit, deep research, judge panel, migration, large-scale sort)
  - user wants to fan out coding agents to mutate a repo in parallel with patch evidence
  - user mentions the `flow` tool or github.com/Illuminfti/flow
  - need cost-aware model routing across providers or via a subscription (ChatGPT Pro / Claude Max / Grok)
  - need a cross-vendor alternative to Claude Code Workflow with budget gates and crash-resume
---

# flow — dynamic workflows for any agent, any model

`flow` is a tiny Python engine where **your code owns the orchestration** and models
do bounded leaf work that runs concurrently, routes by cost, enforces JSON schemas,
retries transient failures, and resumes after a crash. Repo:
https://github.com/Illuminfti/flow

## Install (once)

```bash
pipx install "git+https://github.com/Illuminfti/flow"   # or: uv tool install / pip install
flow init                                                # writes ~/.config/flow/config.json or config.yaml
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
`wf.code(prompt, *, agent, label, workspace, isolation, schema, continue_id, reserve_tokens, timeout, required)` ·
`wf.local(fn, *args)` · `wf.parallel([thunk,...])` · `wf.pipeline(items, *stages)` ·
`wf.workflow(child_fn, inputs)` · `wf.phase(name)` · `wf.log/notify` ·
`wf.spend()/remaining()/has_headroom()` · `wf.block(text)` / `wf.resolve_blocks(text)` ·
`wf.loop(spec=LoopSpec(...), step=..., verify=...)` ·
`wf.merge(patches, *, repo, target_branch, checks, canary, auto_merge, max_repairs)`.
Build a list of zero-arg lambdas for `parallel`; bind loop vars with defaults
(`lambda d=d: ...`).

## v3: agentic leaves — `wf.code`

Fan out full CLI coding agents as leaves. Each agent uses its own tools (read/
edit files, run commands), returns a structured receipt, and runs inside the
budget gate and crash-resume journal.

```python
VERDICT = {"type": "object", "required": ["verdict"]}

def run(wf, args):
    wf.phase("build")
    # Run three agents in parallel, each in its own isolated worktree
    receipts = wf.parallel([
        lambda task=task: wf.code(
            task["prompt"], agent="coder",
            workspace=args["repo"], isolation="worktree",
            schema=VERDICT, label=task["label"])
        for task in args["tasks"]
    ])
    # receipts[i]["patch"]["files"]  — what each agent changed
    # receipts[i]["session_id"]      — chain a follow-up with continue_id=
    # live repo is untouched; apply patches selectively
    return receipts
```

Configure agents in `.flow.yaml` — `codex` is $0 default coder (ChatGPT Pro),
`claude` is the cross-vendor reviewer:

```yaml
agents:
  coder:
    harness: codex
    sandbox: workspace-write
    reserve_tokens: 30000
  reviewer:
    harness: claude
    model: sonnet
    allowed_tools: [Read, Grep, Glob]
    reserve_tokens: 30000
```

Receipt shape: `{value, text, session_id, patch: {changed, files, patch}, workspace, leaf_id, agent, spend}`.
Chain leaves: `wf.code(..., continue_id=first["session_id"])`.
Full guide: `docs/agents.md`.

## v4: ship a backlog — `wf.merge`

Turn the fan-out of agent patches into merged, proven code. Each patch applies
onto a fresh integration branch, is proof-gated against the repo's real test
suite, repaired by a bounded agent leaf on conflict or red, and (when the repo
is allowlisted) auto-merged to the target branch with an auto-reverting canary.

```python
def run(wf, args):
    wf.phase("implement")
    receipts = wf.parallel([
        (lambda t=t: wf.code(t["prompt"], agent="coder", workspace=args["repo"],
                             isolation="worktree", label=t["id"], required=False))
        for t in args["backlog"]])
    wf.phase("ship")
    return wf.merge(receipts, repo=args["repo"], target_branch="main",
                    checks=[{"name": "test", "command": ["python3", "-m", "pytest", "-q"]}],
                    canary=[{"name": "canary", "command": ["python3", "-m", "pytest", "-q"]}],
                    auto_merge=True)
```

Safety doctrine — **autonomous merge is gated, not trusted**:

- **Money fence (fail-closed).** `auto_merge` promotes only if the repo is in
  config `merge.allowlist`. Empty allowlist = withheld everywhere. **NEVER add a
  liquidator / live-money repo.**
- **Test-tamper guard.** A patch that modifies existing tests is routed to the
  cross-vendor reviewer and fails closed unless explicitly accepted.
- **Flake quarantine.** A check that flips on a clean re-run neither blocks nor ships.
- **Auto-revert tripwire.** A post-merge canary failure reverts the target to its
  pre-merge SHA and exiles the batch.

MergeResult: `{merged:[ids], exiled:[ids], merged_to_target, reverted, integration_branch, outcomes:[{status, proofs, review}], canary}`.
Worked example: `examples/v4_autonomous_backlog.py`. Full guide: `docs/merge.md`.

## v2: blocks + bounded loops (the big levers)

**Shared-context dedup** — store a shared blob once, embed the ref in every sibling
prompt; the budget charges the block once per scope instead of once per sibling
(wide fan-out was measured 53–89% redundant input without this):

```python
ref = wf.block(args["big_context"])          # sha256-addressed, stored once
wf.agent(f"Analyze lens X.\n\nContext: {ref}", ...)   # sibling embeds the ref
```

**Iterate-to-goal loop** — bounded, crash-resumable, gate-accepted:

```python
from flow import LoopSpec
return wf.loop(
    spec=LoopSpec(goal="produce a verified synthesis", max_iterations=3,
                  required_gates=["schema", "verifier"], stall_limit=2,
                  verifier_policy={"tier": "verify", "must_differ_from_executor": True}),
    step=lambda wf, ctx: wf.agent(..., tier="quality"),          # ctx has prev + prev_gates
    verify=lambda wf, ctx: wf.agent(f"Adversarially review: {ctx['candidate']}. "
                                    'Return {"verdict":"accept"|"reject","issues":[...]}',
                                    tier="verify",
                                    schema={"type": "object", "required": ["verdict"]}))
```

Stops deterministically: goal_met > max_iterations > no_progress (stall) >
budget_exhausted > cancelled. Every stop writes `handoff-report.json` with a
bounded `next_bounded_action`; completed iterations never rerun after a crash;
a required verifier that resolves to the executor's (provider, model) fails
closed (`VerifierIdentityError`) — no self-grading. Details: `docs/loop.md`.

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
4. For agentic coding tasks: use `wf.code` with `agent="coder"` (codex, $0) for
   generation and `agent="reviewer"` (claude sonnet) for read-only review. Always
   set `isolation="worktree"` when the agent will mutate files.
5. Always set a budget; `reserve_tokens` per agent should be ≥ the expected repo
   context size. Agent leaves ingest far more than their prompt.
6. Inspect `flow trace <run_id>` after; for long agentic runs use `flow watch <run_id>`.
7. For a real run, verify against the trace (routed model + cost per leaf), not the plan.

Full agent setup guide: `AGENTS.md` in the repo. Full v3 agent reference: `docs/agents.md`.
