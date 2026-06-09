# Comparison

`flow` is not a replacement for an agent. It is a harness for agents and scripts that need dynamic, concurrent, inspectable work.

| Capability                               | flow | Embedded agent workflow tools |
| ---------------------------------------- | ---- | ----------------------------- |
| Runs outside one vendor app              | yes  | often no                      |
| Ordinary Python workflow scripts         | yes  | varies                        |
| Per-leaf model routing                   | yes  | varies                        |
| Leaf-level crash resume across processes | yes  | often session-bound           |
| Cost prediction and budget gates         | yes  | varies                        |
| Custom backends and CLIs                 | yes  | usually limited               |
| Native app integrations                  | no   | often yes                     |
| Built-in human UI                        | no   | often yes                     |

## When flow wins

- You want a reusable workflow file in a repo.
- You need one leaf on a cheap model, another on a stronger model, and a local reducer after both.
- You need crash-resume and traceable run artifacts.
- You want to expose workflow execution to many agents or CLIs.

## When embedded systems win

- The workflow depends on a proprietary app's native tools or UI.
- You need a human review surface built into the host app.
- You do not need portability or crash-resumable journals.

Competitor behavior changes. Treat this page as a tradeoff map, not a permanent scorecard.

---

## v3: flow `wf.code` vs Claude Code Workflow

With v3, flow becomes a direct alternative to Claude Code's built-in Workflow
tool for agentic coding tasks. The comparison is honest — each has genuine
advantages.

|                                        | flow `wf.code`                                                                                                              | Claude Code Workflow                                |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| **Vendor**                             | Any: codex ($0 via ChatGPT Pro), claude, or any generic harness                                                             | Claude only                                         |
| **Pre-spend budget gate**              | Hard token/USD/call ceiling enforced before harness launch; replays correctly across SIGKILL restarts (≤1× ceiling, not N×) | Varies; not designed for hard pre-spend enforcement |
| **Worktree isolation**                 | `isolation="worktree"`: each agent runs in a detached git worktree; live repo never mutated; patch artifact returned        | Not available                                       |
| **Parallel mutation on one repo**      | Safe: each agent gets its own worktree                                                                                      | Not designed for it                                 |
| **Crash resume**                       | Completed agent leaf never reruns; crashed-mid-flight leaf reruns clean (worktree is fresh)                                 | Session-bound; varies                               |
| **Identity-distinct verification**     | `wf.loop` verifier enforces `must_differ_from_executor`; refuses self-grading                                               | Manual                                              |
| **Any-vendor routing**                 | codex (ChatGPT Pro, $0), claude (Claude Max), generic (any CLI)                                                             | Claude only                                         |
| **Interactive UI**                     | `flow watch <run_id>` live progress (in progress); `flow trace` after                                                       | Richer built-in interactive UI                      |
| **Schema enforcement on agent output** | JSON Schema validated; repair via continuation (not re-run)                                                                 | Manual                                              |
| **Persisted transcripts**              | Full harness stream at `run_dir/agents/*.transcript.jsonl`                                                                  | In-app session history                              |

### When to use flow `wf.code`

- You need any-vendor routing: free Codex ($0 under ChatGPT Pro) for coding
  leaves, Claude for review, without being locked to one host app.
- You need parallel agents mutating the same repo without trampling each other.
- You need a hard pre-spend budget that survives crashes and restarts.
- You want patch evidence for every agent's changes before deciding what to apply.
- You need SIGKILL-proof crash resume at the leaf level.
- The workflow is automated (no human clicking) and needs to run in CI or on a VPS.

### When Claude Code Workflow wins

- You are already in the Claude Code interactive session and want to stay there.
- You need the rich built-in human UI (file diffs in-app, conversation history,
  interactive approval).
- The task is single-agent and doesn't need cross-vendor routing or worktree
  isolation.
- You do not need crash-resumable journals or pre-spend budget enforcement.
