# Agentic leaves — `wf.code` (v3)

`wf.code` dispatches a bounded task to a full CLI coding agent — Codex, Claude
Code, or any configured harness. The agent uses its own tools (read/edit files,
run commands, own a workspace) and returns a structured receipt. Everything runs
inside the engine: budget-gated, journaled, crash-resumable, schema-enforced.

## Configuring agents

Agents are declared in the `agents:` section of your `.flow.yaml` / `.flow.json`.
`wf.code(agent="name")` looks up the entry; an unknown name raises `RouterError`
immediately.

```yaml
# .flow.yaml
agents:
  coder:
    harness: codex # codex | claude | generic (required)
    sandbox: workspace-write # codex only: read-only | workspace-write | danger-full-access
    reserve_tokens: 30000 # budget reserve floor before launch (default 30000)
    timeout_s: 600 # per-leaf timeout in seconds (default 1800)
    model: "" # harness model override (optional)
    bin: "" # absolute path to harness executable (optional override)
    system_prompt: "" # appended to every prompt for this agent (optional)

  reviewer:
    harness: claude
    model: sonnet
    allowed_tools: [Read, Grep, Glob] # claude --allowedTools
    # skip_permissions: true            # WARNING: bypasses all Claude Code permission prompts
    reserve_tokens: 30000
    timeout_s: 600

  hermes:
    harness: generic
    cmd_template:
      [
        "hermes",
        "chat",
        "--provider",
        "openai-codex",
        "-m",
        "gpt-5.5",
        "{prompt}",
      ]
    reserve_tokens: 5000
```

### All config keys

| Key                | Applies to    | Description                                                                                                                                                                                                              |
| ------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `harness`          | all           | `codex`, `claude`, or `generic`. Required.                                                                                                                                                                               |
| `model`            | codex, claude | Model string passed to the harness (`-m` / `--model`).                                                                                                                                                                   |
| `bin`              | all           | Override the harness executable path (useful in CI or virtualenvs).                                                                                                                                                      |
| `sandbox`          | codex         | Sandbox level: `read-only`, `workspace-write` (default), or `danger-full-access` (see below).                                                                                                                            |
| `allowed_tools`    | claude        | List of tool names passed to `--allowedTools`.                                                                                                                                                                           |
| `skip_permissions` | claude        | **WARNING: disables all permission prompts.** Only use on fully de-gated boxes. Passes `--dangerously-skip-permissions`.                                                                                                 |
| `system_prompt`    | all           | String prepended to every prompt for this agent as a system instruction.                                                                                                                                                 |
| `cmd_template`     | generic       | argv template; `{prompt}` and `{workspace}` are substituted.                                                                                                                                                             |
| `reserve_tokens`   | all           | Token floor the budget gate reserves before the harness launches. Default 30 000. Agent leaves ingest far more than their prompt (repo context, tool output) — set this high enough to prevent premature budget refusal. |
| `timeout_s`        | all           | Per-leaf timeout in seconds. Default 1800 (30 min).                                                                                                                                                                      |

### Sandbox levels (codex)

| Level                | What it means                                                                                                                                        |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `read-only`          | Agent can read files but not write or run commands.                                                                                                  |
| `workspace-write`    | Agent can read and write within its workspace directory. Default.                                                                                    |
| `danger-full-access` | **WARNING:** passes `--dangerously-bypass-approvals-and-sandbox`. No isolation at all. Only safe when the workspace is already a throwaway worktree. |

For parallel runs on a shared repo, use `isolation="worktree"` (see below) — each
agent then gets its own detached worktree, so even `workspace-write` is safe.

---

## `wf.code` — signature and receipt

```python
receipt = wf.code(
    prompt,                  # task description string
    *,
    agent="coder",           # name of the agents: config entry
    label=None,              # leaf label for traces/resume (auto-generated if omitted)
    workspace=None,          # cwd for the harness; defaults to os.getcwd()
    isolation=None,          # "" (default) or "worktree"
    schema=None,             # JSON Schema dict for the final answer (optional)
    continue_id="",          # session_id from a prior receipt → resume that session
    reserve_tokens=None,     # override agents.<name>.reserve_tokens for this leaf
    timeout=None,            # override agents.<name>.timeout_s for this leaf
    required=True,           # False → return None on failure instead of raising
)
```

**Returns** a receipt dict (or `None` when `required=False` and the leaf fails):

```python
{
    "value":      ...,          # schema-parsed final answer (or raw text if no schema)
    "text":       "...",        # raw harness output (may be truncated; full text in transcript)
    "session_id": "thread-id",  # continuation handle for a follow-up wf.code(continue_id=...)
    "patch":      {             # worktree change evidence (isolation="worktree" only)
        "changed": True,
        "files":   ["src/foo.py", "tests/test_foo.py"],
        "patch":   "artifacts/abcd1234.patch",   # path relative to run_dir
    },
    "workspace":  "/path/to/worktree",   # where the agent actually ran
    "leaf_id":    "...",
    "agent":      "coder",
    "spend":      {
        "input_tokens":  12400,
        "output_tokens": 820,
        "usd":           0.0,    # $0 for codex (ChatGPT subscription)
    },
}
```

---

## Worktree isolation

When `isolation="worktree"`, the engine creates a detached `git worktree` for
the repo containing `workspace`, runs the agent there, captures all changes as a
binary patch artifact, then removes the worktree. The live repo working tree is
never mutated.

```python
def run(wf, args):
    wf.phase("fanout")
    receipts = wf.parallel([
        lambda i=i: wf.code(
            f"Implement feature {i}",
            agent="coder",
            workspace=args["repo"],
            isolation="worktree",
            label=f"feature-{i}",
        )
        for i in range(3)
    ])
    # receipts[i]["patch"]["files"]  — what each agent changed
    # receipts[i]["patch"]["patch"]  — relative path to the binary patch
    # the real repo is untouched until you apply one
    return receipts
```

**Parallel safety.** Multiple agents running `isolation="worktree"` on the same
repo path are safe: each leaf gets its own **independent local clone** (`git
clone --local`, hardlinked objects — cheap) under `run_dir/worktrees/<leaf_id>/`,
checked out at the source's current HEAD. A clone is used rather than a linked
`git worktree` because a coding-agent sandbox (Codex) resolves its writable root
through the shared `.git` of a linked worktree and writes to the _main_ working
tree — four parallel agents would clobber each other in the source repo. An
independent clone has its own `.git`, so the agent's repo-root resolution stays
inside it. The change set is captured (committed + uncommitted, vs the base
commit, minus build noise) and the clone is removed.

> **Footgun — never name the original repo path in the prompt.** With
> `isolation="worktree"` the agent runs in the clone (its cwd), not in `repo`.
> If the prompt says "work in the repo at /path/to/repo", the agent will navigate
> to that absolute path and write to the **live source**, bypassing isolation
> entirely — the clone stays empty and `patch["changed"]` is `False`. Tell the
> agent to work in **its current working directory**; the harness already runs
> it there (`-C <clone>`).

**Patch evidence.** `receipt["patch"]["patch"]` is a path relative to `run_dir`
(e.g. `"artifacts/abcd1234.patch"`). Join with `run_dir_for(run_id)` to get the
absolute path. The patch is a binary-capable unified diff suitable for
`git apply`.

**Clean workspaces.** A worktree with no changes (agent ran but produced no
file mutations) sets `patch["changed"] = False` and `patch["patch"] = ""`.

**Resume semantics.** A completed agent leaf (including one that ran in a
worktree) is never re-executed on `flow resume` — the cached receipt is returned.
A leaf that crashed mid-flight re-executes from scratch. Use `isolation="worktree"`
for safe re-execution: the worktree is fresh each time, so reruns never see
prior partial mutations.

---

## Continuation — chaining sessions

`session_id` in a receipt is the harness session or thread handle. Pass it as
`continue_id` to chain a follow-up leaf into the same session instead of starting
fresh — the agent retains its prior context.

```python
def run(wf, args):
    wf.phase("build")
    first = wf.code("scaffold the module", agent="coder",
                    workspace=args["ws"], label="step1")

    second = wf.code("now add tests",      agent="coder",
                     workspace=args["ws"], label="step2",
                     continue_id=first["session_id"])
    return second
```

For **codex**, continuation issues `codex exec resume <thread_id>`.
For **claude**, continuation passes `--resume <session_id>`.

`continue_id` also changes the leaf's identity hash, so two chained leaves are
tracked as separate journal entries and resume independently.

---

## Schema enforcement and schema repair

Pass a JSON Schema dict as `schema` to enforce structure on the agent's final
answer. For `codex`, the schema is written to a temp file and passed via
`--output-schema` (native enforcement). For `claude` and `generic`, the schema
instruction is appended to the prompt and the response is validated post-hoc.

When the first response fails validation, the engine performs **schema repair via
continuation**: it resumes the existing session with the exact validation error
and asks the agent to fix only the output format — never re-running the full task.
This is cheaper and context-preserving compared to a fresh re-run.

```python
VERDICT = {"type": "object", "required": ["verdict"],
           "properties": {"verdict": {"type": "string", "enum": ["accept", "reject"]}}}

receipt = wf.code("Review buggy.py and decide.", agent="reviewer",
                  workspace=repo, schema=VERDICT, label="review")
# receipt["value"] is guaranteed to be {"verdict": "accept"|"reject"}
```

---

## Budget and `reserve_tokens`

Agent leaves are open-ended: the harness reads repo files, runs tools, and
produces multi-turn output. The budget gate cannot estimate true cost from
prompt length alone.

`reserve_tokens` (config default 30 000; overridable per leaf) is the floor the
budget gate reserves **before** the harness launches. The real spend (from actual
`input_tokens` / `output_tokens` in the harness result envelope) is committed
after the run; the reservation is released and replaced with truth.

If the remaining budget is below `reserve_tokens`, the leaf is rejected before
the harness is ever invoked:

```python
# Budget too small — agent never launches; leaf fails (or returns None if required=False)
rep = run_workflow(run_fn=run, args={}, budget={"max_tokens": 500})
# rep["warnings"][0]["error"] contains "budget"
```

Set `reserve_tokens` conservatively high for agents that read large repos.

---

## Transcripts

Every agent leaf appends its full harness output to
`run_dir/agents/<leaf_id_prefix>.transcript.jsonl`. This is the raw stream
(codex JSONL events, claude JSON envelope, generic stdout). It is written even
when the leaf fails, so every agentic decision is auditable after the run.

```bash
flow trace <run_id>          # per-leaf summary including agent + session_id
ls ~/.local/share/flow/<run_id>/agents/   # raw transcripts
```

Transcripts are observability only — a write failure never causes a leaf
failure.

---

## Harness adapters in detail

### codex

Runs `codex exec [resume <thread_id>] --json --skip-git-repo-check [-m model] [-s sandbox | --dangerously-bypass-approvals-and-sandbox] [-C workspace] [--output-schema schema_file] <prompt>`.

- `stdin` is detached (`subprocess.DEVNULL`) — codex reads non-TTY stdin as
  additional input and hangs otherwise.
- Thread id is parsed from the `thread.started` JSONL event.
- Real usage from the `turn.completed` event (`input_tokens`,
  `output_tokens`, `reasoning_output_tokens`).
- `--output-schema` is only passed on the first call; repair turns use
  `resume` instead.
- Cost is `$0` when running via ChatGPT Pro subscription.

### claude

Runs `claude -p <prompt> --output-format json [--model model] [--resume session_id] [--allowedTools tools] [--dangerously-skip-permissions] [--add-dir dir] [--append-system-prompt prompt]`.

- Result is the last JSON line of stdout (the result envelope).
- `session_id` from `env["session_id"]`.
- Real cost from `env["total_cost_usd"]`.
- Input tokens = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.

### generic

Runs the `cmd_template` argv with `{prompt}`, `{workspace}`, and `{model}`
substituted. stdout is the full answer text; token usage is estimated from
character count. No continuation support.

---

## Examples

**Fan-out three agents, review patches:**

```python
def run(wf, args):
    wf.phase("implement")
    receipts = wf.parallel([
        lambda task=task: wf.code(
            task["prompt"], agent="coder",
            workspace=args["repo"], isolation="worktree",
            label=task["label"])
        for task in args["tasks"]
    ])

    wf.phase("review")
    verdicts = wf.parallel([
        lambda r=r: wf.code(
            f"Review this patch and decide:\n{open(r['patch']['patch']).read()}",
            agent="reviewer", workspace=args["repo"],
            schema={"type": "object", "required": ["verdict"]},
            label=f"review-{r['agent']}")
        for r in receipts if r and r["patch"]["changed"]
    ])
    return {"receipts": receipts, "verdicts": verdicts}
```

**Codex as the $0 default coder, Claude as cross-vendor reviewer:**

```yaml
agents:
  coder:
    harness: codex
    sandbox: workspace-write
    reserve_tokens: 40000
  reviewer:
    harness: claude
    model: sonnet
    allowed_tools: [Read, Grep, Glob, Bash]
    reserve_tokens: 30000
```

`coder` is free under a ChatGPT Pro subscription. `reviewer` uses Claude's
native tool surface for read-only code inspection — it never mutates the
workspace, so no `isolation` needed.
