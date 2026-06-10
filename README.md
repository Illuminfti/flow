<div align="center">

# flow

### Your code orchestrates. Models — and full coding agents — do bounded work. Outcomes ship.

**A tiny Python runtime for multi-model LLM workflows and agentic coding tasks — concurrent,
budgeted, schema-validated, crash-resumable, able to iterate until an independent verifier
accepts the result, fan out full CLI coding agents into isolated workspaces with patch evidence,
and (v4) drive a patch set through a proof-gated rebase queue to merged, proven commits on the
target branch — zero-touch.**

[![tests](https://github.com/Illuminfti/flow/actions/workflows/tests.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/tests.yml)
[![install smoke](https://github.com/Illuminfti/flow/actions/workflows/install-smoke.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/install-smoke.yml)
[![lint](https://github.com/Illuminfti/flow/actions/workflows/lint.yml/badge.svg)](https://github.com/Illuminfti/flow/actions/workflows/lint.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![core deps](https://img.shields.io/badge/core%20runtime%20deps-0-orange)
![license](https://img.shields.io/badge/license-MIT-green)

</div>

---

## What is this?

You write one Python function. It fans work out to LLMs — and, in v3, to full
CLI coding agents (Codex, Claude Code, any harness) — checks their answers, and
combines the results. In v4, it drives a set of patches through a proof-gated
rebase queue and ships merged, proven commits to the target branch. `flow` makes
that function **operationally safe**: every leaf (model call or agentic task)
runs concurrently, must return valid JSON (malformed output gets self-repaired),
counts against a hard token/dollar budget, and is journaled to disk — kill the
process at any point and `flow resume` picks up exactly where it died, without
re-paying for finished work. Coding agents run in isolated git worktrees and
hand back patch artifacts; `wf.merge` turns those artifacts into proven commits.

```mermaid
flowchart LR
    YOU["your Python<br/>run(wf, args)"] -->|"wf.agent(...) × N<br/>wf.code(...) × N"| ENGINE
    subgraph ENGINE["flow runtime"]
        direction LR
        R["router<br/>cheapest capable model<br/>or configured agent"] --> B["budget gate<br/>tokens · $ · calls · deadline"]
        B --> X["concurrent execution<br/>+ schema validation<br/>+ self-repair"]
    end
    ENGINE --> M1["GPT / Claude / DeepSeek /<br/>Ollama / any CLI / local fn"]
    ENGINE --> A1["Codex CLI ($0)<br/>Claude Code<br/>any agent harness"]
    ENGINE --> J[("journal<br/>(crash-proof)")]
    J -.->|"flow resume"| ENGINE
```

Any model, any provider — API key **or** the subscriptions you already pay for
(ChatGPT Pro, Claude Max). Zero third-party runtime dependencies.

## Why you'd want it

| You want to…                                          | flow gives you                                                                                                                                                |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fan out 20 checks / lenses / files at once            | `wf.parallel`, `wf.pipeline` — real concurrency, per-leaf failure policy                                                                                      |
| Trust model output                                    | JSON Schema enforced at every leaf; bad output is auto-repaired with the exact validation error, then fails loudly                                            |
| Not wake up to a $400 bill                            | Hard budgets (tokens, USD, calls, deadline) enforced **before** each call — and still enforced across crash-restarts                                          |
| Keep big shared context from being billed N times     | `wf.block`: store it once, fan-out siblings get a reference (wide fan-outs measured 53–89% redundant without this)                                            |
| Iterate until the result is actually good             | `wf.loop`: bounded retry-until-verified, with acceptance gates and a **different model as the verifier** — no self-grading                                    |
| Survive crashes, reboots, rate-limit deaths           | fsync'd write-ahead journal; `flow resume` skips all completed work                                                                                           |
| See what happened                                     | `flow trace`: per-leaf model, cost, latency, retries, repairs                                                                                                 |
| Fan out real coding agents to mutate code in parallel | `wf.code` + `isolation="worktree"`: N agents run in isolated worktrees, changes come back as patch artifacts — live repo untouched                            |
| Ship a backlog autonomously — zero human in the loop  | `wf.merge`: proof-gate each patch, repair conflicts/red via bounded agent leaves, auto-merge to the target branch, auto-revert if the post-merge canary fails |

**Skip it** for one-shot prompts and deterministic ETL — workflows multiply
calls; always set a budget.

## 60-second start

```bash
pipx install "git+https://github.com/Illuminfti/flow"

flow self-test --offline    # proves the engine works — no key, no network
export OPENAI_API_KEY=sk-...    # or DeepSeek/Ollama/Anthropic/subscriptions
flow init && flow doctor

# the model authors AND runs the workflow:
flow run --nl "review this repo for the three highest-risk release blockers" \
  --budget '{"max_usd":1,"max_calls":8}'

flow trace <run_id>         # per-leaf model / cost / latency / retries
```

Extras: `flow[yaml]` (YAML config) · `flow[schema]` (full JSON Schema) ·
`flow[anthropic]` (native SDK) · `flow[all]`.

## A real workflow in 30 lines

Fan out three review lenses, then adversarially verify each finding with a
cheaper model:

```python
FINDING = {"type": "object", "required": ["title", "severity"],
           "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}

def run(wf, args):
    wf.phase("review")
    findings = wf.parallel([
        lambda lens=lens: wf.agent(
            f"Review {args['target']} for one {lens} issue. JSON only.",
            label=f"review:{lens}", schema=FINDING, tier="quality")
        for lens in ("correctness", "security", "performance")])

    wf.phase("verify")
    return wf.parallel([
        lambda f=f: wf.agent(f"Try to refute this finding: {f}",
                             label="verify", tier="cheap", required=False)
        for f in findings if f])
```

```bash
flow run review.py --args '{"target":"src/"}' --budget '{"max_usd":1}'
```

Your code is deterministic control flow; models only do the bounded leaf work
you hand them. That inversion is the whole design.

## Iterate until verified — `wf.loop` (v2)

The piece most frameworks fake: a **bounded** loop that keeps improving a
candidate until an **independent** verifier accepts it — and stops itself when
it's stuck, out of budget, or done.

```mermaid
flowchart LR
    S["step<br/>(executor model)"] --> V["verify<br/>(different model)"]
    V --> G{"gates<br/>schema → verifier"}
    G -->|all pass| DONE(["goal_met ✓"])
    G -->|reject| S
    G -.->|"same evidence twice"| STALL(["no_progress — stop"])
    G -.->|"ceiling hit"| BUDGET(["budget_exhausted — stop"])
    DONE & STALL & BUDGET --> H["handoff report:<br/>what passed, what failed,<br/>exact next action"]
```

```python
from flow import LoopSpec

def run(wf, args):
    ref = wf.block(args["shared_context"])        # stored once, billed once

    return wf.loop(
        spec=LoopSpec(goal="a verified report section", max_iterations=5,
                      required_gates=["schema", "verifier"], stall_limit=2,
                      verifier_policy={"tier": "verify",
                                       "must_differ_from_executor": True}),
        step=lambda wf, ctx: wf.agent(
            f"{ref}\n\nDraft the section. Fix: {ctx['prev_gates']}",
            label=f"draft:{ctx['iteration']}", schema=SECTION, tier="quality"),
        verify=lambda wf, ctx: wf.agent(
            f'Adversarially review: {ctx["candidate"]}. '
            'JSON: {"verdict":"accept"|"reject","issues":[...]}',
            label=f"verify:{ctx['iteration']}", tier="verify"))
```

What makes it trustworthy rather than vibes:

- **No self-grading.** If the verifier resolves to the executor's
  (provider, model), the loop refuses to start (`VerifierIdentityError`) —
  it never silently downgrades to a model approving its own work.
- **Deterministic stops**, in precedence order:
  `goal_met → max_iterations → no_progress → budget_exhausted → cancelled`.
  Stall detection fingerprints failures; two iterations of identical evidence
  stop the loop _before_ the token wall, not after.
- **Cheap gates run first.** Deterministic checks (schema, artifact) cost
  nothing; the LLM verifier is skipped entirely when they already failed.
- **Every stop writes a handoff** (`handoff-report.json`): verified/rejected
  split, recurring failure signatures, spend, and a concrete
  `next_bounded_action` — never a bare `"failed"`.
- **Crash-resumable.** A completed iteration never reruns; budgets replay from
  the journal, so the ceiling holds across N restarts (≤1×, not N×).

Full guide: [`docs/loop.md`](docs/loop.md).

## Shared context without the N× bill — `wf.block` (v2)

Wide fan-out has a hidden cost: every sibling re-ingests the same context, and
it's the dominant spend at scale — real runs measured **53–89% redundant input
tokens**, enough to breach a 900k ceiling on work that fits in 250k.

```mermaid
flowchart LR
    subgraph before["without blocks"]
        C1["context 22KB"] --> L1["leaf 1"]
        C2["context 22KB"] --> L2["leaf 2"]
        C3["context 22KB"] --> L3["leaf N…"]
    end
    subgraph after["with wf.block"]
        BS[("block store<br/>22KB, stored once")] -.ref.-> M1["leaf 1"]
        BS -.ref.-> M2["leaf 2"]
        BS -.ref.-> M3["leaf N…"]
    end
    before ~~~ after
```

```python
ref = wf.block(huge_context)              # sha256-addressed, charged once
wf.agent(f"Analyze lens X.\n\n{ref}")     # sibling embeds the ref envelope
```

The budget charges the block once per scope instead of once per sibling, and
the dedup rate is **measured** from persisted per-leaf input hashes
(`flow.blocks.dedup_report`), not estimated.

**Receipt:** a real 25-leaf research run that died at 906,313 tokens against a
900k ceiling under v1 was replayed through v2 under the _same_ ceiling —
completed at 205,073 total (input 822,120 → 135,619, **−83.5%**), with the
loop stopping on `goal_met` via an independent verifier.

## Agentic leaves — `wf.code` (v3)

v3 makes each leaf a **full CLI coding agent** — Codex, Claude Code, or any
configured harness — that uses its own tools (read/edit files, run commands,
own a workspace) and returns a receipt with the final answer, real token spend,
a continuation handle, and patch evidence when run in an isolated worktree.
Everything stays inside the engine's budget gate, crash-resume journal, and
schema enforcement.

```mermaid
flowchart LR
    YOU["wf.parallel([<br/>  wf.code(task_A, isolation='worktree'),<br/>  wf.code(task_B, isolation='worktree'),<br/>  wf.code(task_C, isolation='worktree'),<br/>])"] --> WT
    subgraph WT["worktree isolation"]
        direction TB
        W1["detached worktree A"] --> A1["agent A<br/>(Codex / Claude / …)"]
        W2["detached worktree B"] --> A2["agent B"]
        W3["detached worktree C"] --> A3["agent C"]
    end
    A1 --> P1["patch artifact A<br/>{changed, files, patch}"]
    A2 --> P2["patch artifact B"]
    A3 --> P3["patch artifact C"]
    P1 & P2 & P3 --> R["review / merge step<br/>(your code)"]
    R -.->|"live repo<br/>never touched"| REPO[("repo")]
```

**Real example** (from `tests/test_live_agents.py`):

```python
VERDICT = {"type": "object", "required": ["verdict"]}

def run(wf, args):
    wf.phase("build")
    return wf.code(
        "Create fizzbuzz.py with a fizzbuzz(n) function. "
        'Reply with JSON {"verdict": "done"}.',
        agent="coder", workspace=str(args["repo"]),
        isolation="worktree", schema=VERDICT, label="fizzbuzz")
```

`wf.code` returns a receipt dict:

```python
{
    "value":      {"verdict": "done"},   # schema-parsed final answer
    "text":       "...",                 # raw harness output
    "session_id": "thread-abc",          # continuation handle
    "patch":      {"changed": True,      # worktree evidence
                   "files": ["fizzbuzz.py"],
                   "patch": "artifacts/abcd1234.patch"},
    "workspace":  "/path/to/worktree",
    "leaf_id":    "...",
    "agent":      "coder",
    "spend":      {"input_tokens": 12400, "output_tokens": 820, "usd": 0.0},
}
```

**Session continuation** — chain leaves without re-running from scratch:

```python
first  = wf.code("start the work", agent="coder", workspace=ws, label="step1")
follow = wf.code("now finish it",  agent="coder", workspace=ws, label="step2",
                 continue_id=first["session_id"])
```

**Config** — all harness behavior lives in `agents:`, not per-leaf code:

```yaml
agents:
  coder:
    harness: codex
    sandbox: workspace-write # read-only | workspace-write | danger-full-access
    reserve_tokens: 30000
    timeout_s: 600
  reviewer:
    harness: claude
    model: sonnet
    allowed_tools: [Read, Grep, Glob]
    reserve_tokens: 30000
    timeout_s: 600
```

**vs Claude Code Workflow** — honest comparison:

|                                     | flow `wf.code`                               | Claude Code Workflow |
| ----------------------------------- | -------------------------------------------- | -------------------- |
| Vendor                              | any (codex=$0, claude, generic)              | Claude only          |
| Pre-spend budget gate               | yes — SIGKILL-proof, replays across restarts | varies               |
| Worktree isolation + patch evidence | yes                                          | no                   |
| Parallel agent mutation (same repo) | safe (isolated worktrees)                    | not designed for it  |
| Crash resume                        | yes — completed agent leaf never reruns      | session-bound        |
| Identity-distinct verification      | yes (`wf.loop` verifier)                     | manual               |
| Interactive UI                      | `flow watch <run_id>` (live progress)        | richer built-in UI   |

Full guide: [`docs/agents.md`](docs/agents.md).

## Ship a backlog autonomously — `wf.merge` (v4)

v3 ended with loose patches — a human had to decide what to apply. v4 drives those
patches to merged, proven commits on the target branch, zero-touch.

```mermaid
flowchart LR
    BL["backlog\n[task A, task B, ...]"] --> FO
    subgraph FO["wf.parallel — one worktree per task"]
        direction TB
        W1["worktree A\ncoder agent"] --> PA["patch A"]
        W2["worktree B\ncoder agent"] --> PB["patch B"]
    end
    PA & PB --> MQ
    subgraph MQ["wf.merge — proof-gated rebase queue"]
        direction TB
        G1["Guard 1: test-tamper?\n→ cross-vendor reviewer"] --> G2
        G2["Guard 2: proof checks\n(flake quarantine)"] --> G3
        G3["Guard 3: canary on target\n(auto-revert tripwire)"]
    end
    MQ -->|"green + allowlisted"| TB[("target branch\nmerged + proven")]
    MQ -->|"red / tamper / conflict"| EX["exiled\n(repair loop first)"]
```

```python
# examples/v4_autonomous_backlog.py — the kill-condition workflow
def run(wf, args):
    repo, target = args["repo"], args.get("target", "main")
    test_cmd = args["test_command"]

    wf.phase("implement")
    receipts = wf.parallel([
        (lambda t=t: wf.code(
            f"Repo: {repo}. Task {t['id']!r}: {t['prompt']}\n\n"
            "Implement cleanly, add/extend tests. Do NOT weaken existing tests. "
            'Reply {"summary": "<what you changed>"}.',
            agent="coder", workspace=repo, isolation="worktree",
            schema={"type": "object", "required": ["summary"]},
            label=t["id"], required=False))
        for t in args["backlog"]
    ])

    specs = [
        {"task_id": t["id"],
         "patch_path": str(wf._engine.run_dir / r["patch"]["patch"]),
         "files": r["patch"]["files"]}
        for t, r in zip(args["backlog"], receipts)
        if r and (r.get("patch") or {}).get("changed")
    ]

    wf.phase("ship")
    result = wf.merge(
        specs, repo=repo, target_branch=target,
        checks=[{"name": "suite", "command": test_cmd, "timeout": 900}],
        canary=[{"name": "canary", "command": test_cmd, "timeout": 900}],
        auto_merge=True, max_repairs=1)

    shipped, total = len(result["merged"]), len(args["backlog"])
    return {"zero_touch_ship_rate": round(shipped / total, 3) if total else 0.0,
            "shipped": shipped, "total": total,
            "merged_to_target": result["merged_to_target"],
            "reverted": result["reverted"]}
```

**`merge:` config** — the repo must be allowlisted for auto-merge to fire:

```yaml
merge:
  allowlist:
    - /abs/path/to/repo # exact match or prefix
  checks:
    - name: suite
      command: [python3, -m, pytest, -q]
      timeout: 900
  canary:
    - name: canary
      command: [python3, -m, pytest, -q]
      timeout: 900
```

**vs Claude Code Workflow — the ship layer.** CC Workflow agents edit files but there
is no built-in proof-gated rebase queue, no auto-merge with a money fence, no auto-revert
tripwire, and no test-tamper guard. flow still has no live interactive UI beyond
`flow watch`; CC Workflow's leaves are first-class CC subagents. v4's new ground
is the _integration + ship_ layer.

Full guide: [`docs/merge.md`](docs/merge.md).

## How a single call actually runs

```mermaid
sequenceDiagram
    participant W as your code
    participant R as router
    participant B as budget
    participant K as backend
    participant J as journal
    W->>R: wf.agent(prompt, schema, tier="cheap")
    R->>R: pick cheapest capable model
    R->>B: reserve estimated tokens
    Note over B: over ceiling? → fail BEFORE spending
    B->>K: call model (retry transient errors)
    K-->>K: schema invalid? repair with exact error
    K->>B: commit real spend
    K->>J: fsync result (resume skips this leaf forever)
    J->>W: validated value
```

Durable leaf identity = hash of script, phase, label, prompt, route, schema,
and options. Same work → same identity → never recomputed, in this run or on
resume.

## The whole API

```python
wf.agent(prompt, *, label, schema, tier, model, provider, backend,
         max_tokens, timeout, tools, tool_approval_gates, required=True)
wf.code(prompt, *, agent, label, workspace, isolation, schema,         # v3: agentic leaf
        continue_id, reserve_tokens, timeout, required=True)
wf.local(fn, *args)                      # deterministic Python as a leaf
wf.parallel([thunk, ...])                # modes: lenient | fail_fast | collect_errors
wf.pipeline(items, stage1, stage2, ...)  # per-item flow, no barriers
wf.workflow(child_fn, inputs)            # nested workflow, shared pools

wf.loop(spec=LoopSpec(...), step, verify)        # v2: bounded iterate-to-goal
wf.block(text) / wf.resolve_blocks(text)         # v2: shared-context dedup

wf.phase(name) · wf.log/notify(...) · wf.spend() · wf.remaining()
wf.has_headroom(min_tokens) · wf.cancelled()
```

```bash
flow run workflow.py --args '{...}' --budget '{"max_usd":1}'
flow run --nl "<task>"            # model authors the workflow, flow validates + runs it
flow resume <run_id> workflow.py  # finish a crashed run, completed work is free
flow trace <run_id>               # per-leaf model/cost/latency/retries/repairs
flow watch <run_id>               # v3: live progress as leaves complete
flow author / list / status / doctor / self-test / init
```

## Backends

| Backend         | Reaches                                                                     | Tools        |
| --------------- | --------------------------------------------------------------------------- | ------------ |
| `openai_http`   | OpenAI, DeepSeek, OpenRouter, Groq, Together, Ollama, any compatible server | yes          |
| `anthropic_sdk` | Claude (native SDK)                                                         | yes          |
| `codex`         | ChatGPT-subscription Codex route ($0 with ChatGPT Pro)                      | fails closed |
| `shell_cmd`     | any authenticated CLI — Claude Code, local models, custom bridges           | fails closed |
| `agent_cli`     | full CLI coding agent harnesses (codex/claude/generic) via `wf.code`        | agent-owned  |
| `local`         | plain Python functions                                                      | n/a          |

Models, tiers, and pricing are pure config — one JSON/YAML file, no code
(`flow init`, then see [`docs/config.md`](docs/config.md) and
[`docs/subscriptions.md`](docs/subscriptions.md)).

## What's tested

168 hermetic tests (no network) run in CI: concurrency, budgets/deadlines/
cancellation, schema repair, crash-resume (real SIGKILL subprocesses), budget
replay across restarts, loop stops/gates/repair-routing/handoffs, deterministic
stop replay, block dedup + double-charge prevention, verifier identity
enforcement, and a v1 regression lock. Live provider routes are
credential-gated. A replay harness re-runs real failed production manifests
under their original ceilings as the performance acceptance bar
(`tests/test_perf_baselines.py`).

```bash
python -m pytest          # the same suite CI runs
flow self-test --offline
```

## Security model

Authored workflows are trusted Python, not a sandbox. Model-authored scripts
are AST-validated against unsafe imports/builtins as an accident guard, not a
containment boundary. Details: [`SECURITY.md`](SECURITY.md) ·
[`docs/security.md`](docs/security.md).

## Docs

[`docs/index.md`](docs/index.md) — map ·
[`docs/agents.md`](docs/agents.md) — agentic leaves (v3) ·
[`docs/loop.md`](docs/loop.md) — loop envelope ·
[`docs/architecture.md`](docs/architecture.md) — internals ·
[`docs/api.md`](docs/api.md) — Python API ·
[`docs/cli.md`](docs/cli.md) — CLI ·
[`docs/config.md`](docs/config.md) — config ·
[`docs/backends.md`](docs/backends.md) — backends ·
[`docs/patterns.md`](docs/patterns.md) — patterns ·
[`docs/testing.md`](docs/testing.md) — verification ·
[`docs/troubleshooting.md`](docs/troubleshooting.md) ·
[`docs/comparison.md`](docs/comparison.md) — positioning

Agent setting this up for yourself? Read [`AGENTS.md`](AGENTS.md).

## License

MIT © Illumi [@Illuminfti](https://github.com/Illuminfti)
