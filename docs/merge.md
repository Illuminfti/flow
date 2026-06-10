# Merge orchestrator — `wf.merge` (v4)

`wf.merge` takes the patch set produced by a fan-out of worktree-isolated agent
leaves and drives it to merged, proven commits on a target branch — sequentially,
each patch gated by real proof receipts, with three guards that make autonomous
merge safe enough to leave running overnight.

v3 left patches on disk. v4 ships them.

## Quick example

```python
from flow import run_workflow

def run(wf, args):
    repo = args["repo"]

    wf.phase("implement")
    receipts = wf.parallel([
        lambda t=t: wf.code(
            f"Task {t['id']}: {t['prompt']}. Add tests. "
            'Reply JSON {"summary": "..."}.',
            agent="coder", workspace=repo, isolation="worktree",
            schema={"type": "object", "required": ["summary"]},
            label=t["id"], required=False)
        for t in args["backlog"]
    ])

    # convert receipts → patch specs
    specs = []
    for t, r in zip(args["backlog"], receipts):
        if r and (r.get("patch") or {}).get("changed"):
            specs.append({
                "task_id": t["id"],
                "patch_path": str(wf._engine.run_dir / r["patch"]["patch"]),
                "files": r["patch"]["files"],
            })

    wf.phase("ship")
    result = wf.merge(
        specs, repo=repo, target_branch=args["target"],
        checks=[{"name": "suite", "command": ["pytest", "-q"], "timeout": 900}],
        canary=[{"name": "canary", "command": ["pytest", "-q"], "timeout": 900}],
        auto_merge=True, max_repairs=1)

    total = len(args["backlog"])
    shipped = len(result["merged"])
    return {
        "zero_touch_ship_rate": round(shipped / total, 3) if total else 0.0,
        "shipped": shipped, "total": total,
        "merged_to_target": result["merged_to_target"],
        "reverted": result["reverted"],
    }
```

The canonical worked example is `examples/v4_autonomous_backlog.py`.

## Signature

```python
result = wf.merge(
    patches,                       # list — see "Patch specs" below
    *,
    repo,                          # str — path to the git repo root (or any path inside it)
    target_branch=None,            # str | None — defaults to the repo's current branch
    checks=None,                   # list[dict] | None — proof gate on each applied patch
    canary=None,                   # list[dict] | None — post-merge checks on the target branch
    auto_merge=False,              # bool — promote to target_branch if all guards pass
    max_repairs=1,                 # int — bounded repair tries per patch (conflict + red)
    repair_agent="coder",          # str — agents: entry for conflict/red repair leaves
    reviewer_agent="reviewer",     # str — agents: entry for test-tamper review leaves
)
```

Returns a `MergeResult` (see below). Also returns the same data as a plain dict
(via `as_dict()`) — callers that index with `result["merged"]` get list of
`task_id` strings.

### Patch specs

`patches` is a list of dicts. Each dict must have `task_id` and one of
`patch_text` (raw unified diff string) or `patch_path` (path to a `.patch`
file). `files` is optional; if absent, it is extracted from the diff.

```python
# from wf.code receipts
{"task_id": "feat-foo", "patch_path": "/run/.../artifacts/feat-foo.patch", "files": [...]}

# inline text
{"task_id": "feat-bar", "patch_text": "diff --git a/...", "files": ["src/bar.py"]}
```

Use `from flow.merge import find_patches_in_run` to convert a list of
`wf.code` receipts into patch specs automatically:

```python
from flow.merge import find_patches_in_run
specs = find_patches_in_run(wf, receipts)
```

## `MergeResult` shape

`wf.merge` returns a `MergeResult` dataclass. It also serializes as a dict
(index it like `result["merged"]` or use `result.as_dict()`).

| Field                | Type                | Meaning                                                                                      |
| -------------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| `repo`               | `str`               | Resolved absolute path to the repo root                                                      |
| `target_branch`      | `str`               | Branch patches were targeted at                                                              |
| `integration_branch` | `str`               | Scratch branch created for this run (`flow/integ-<run_id>`)                                  |
| `merged_to_target`   | `bool`              | True only if the integration branch was fast-merged to target and the canary (if any) passed |
| `target_sha_before`  | `str`               | HEAD SHA of the target branch before this call                                               |
| `target_sha_after`   | `str`               | HEAD SHA after merge (equals `target_sha_before` if not promoted or reverted)                |
| `reverted`           | `bool`              | True if the auto-revert tripwire fired                                                       |
| `merged`             | `list[str]`         | `task_id` values with `status == "merged"`                                                   |
| `exiled`             | `list[str]`         | `task_id` values with any non-merged status                                                  |
| `outcomes`           | `list[TaskOutcome]` | Per-patch detail (see below)                                                                 |
| `canary`             | `dict`              | `ProofBundle.as_dict()` for the post-merge canary run (empty if none)                        |

### `TaskOutcome` fields

| Field     | Type        | Meaning                                                      |
| --------- | ----------- | ------------------------------------------------------------ |
| `task_id` | `str`       | From the patch spec                                          |
| `status`  | `str`       | `merged` / `exiled` / `quarantined_block`                    |
| `reason`  | `str`       | Human-readable detail                                        |
| `files`   | `list[str]` | Files touched by the patch                                   |
| `proofs`  | `dict`      | `ProofBundle.as_dict()` snapshot at decision time            |
| `commit`  | `str`       | Commit SHA on the integration branch (populated on `merged`) |
| `review`  | `dict`      | Reviewer response if the test-tamper guard fired             |

## The integration-branch model

`wf.merge` never writes directly to your target branch during the apply loop.
It creates a scratch branch `flow/integ-<run_id>` forked at the target's HEAD,
then applies patches onto it one at a time as a sequential rebase queue:

1. Fork `flow/integ-<run_id>` from `target_branch` HEAD.
2. For each patch in order: apply → proof → commit (or exile/block).
3. If `auto_merge=True` and the repo is allowlisted: fast-merge the integration
   branch into `target_branch` with `--no-ff`. Run the canary against the
   target. Roll back on failure.

The integration branch accumulates only patches that passed all guards. Patches
that were exiled or blocked are absent from it.

Each patch application does a `git apply --3way --index` onto the current
integration HEAD. A conflict that the 3-way merge cannot resolve triggers
the repair loop (see below) before the patch is exiled.

## Proof gates

### Per-patch checks (`checks`)

`checks` is a list of check descriptors. Each check runs against the integration
branch after the patch is applied. All checks must pass (green) for a patch to
be committed onto the integration branch.

```python
checks=[
    {"name": "suite",  "command": ["pytest", "-q"],         "timeout": 900},
    {"name": "build",  "command": ["python", "-m", "build"], "timeout": 120},
    {"name": "lint",   "command": ["ruff", "check", "."],    "timeout": 60},
]
```

Each check runs via `proof.run_check_quarantined`, which means:

1. Run the check.
2. If it fails, run it again on a clean tree.
3. If the second run passes, the check is **quarantined** (flaky — see below).
4. If both runs fail, it is a real failure and the patch is exiled.

If `checks` is empty or `None`, patches are committed on apply success alone
(the `reason` field will say `"applied (no checks configured)"`). This is
permissible for repos where no automated suite exists, but it means the
integration branch carries no proof.

### `ProofReceipt` fields

Each check execution produces a `ProofReceipt`:

| Field             | Type          | Meaning                                                          |
| ----------------- | ------------- | ---------------------------------------------------------------- |
| `name`            | `str`         | Name from the check descriptor                                   |
| `command`         | `str`         | The command that ran (argv joined)                               |
| `passed`          | `bool`        | `exit_code == 0`                                                 |
| `exit_code`       | `int \| None` | Raw exit code; `None` on timeout or crash-to-launch              |
| `duration_s`      | `float`       | Wall time in seconds                                             |
| `output_tail`     | `str`         | Last 4000 bytes of combined stdout+stderr                        |
| `cwd`             | `str`         | Working directory                                                |
| `quarantined`     | `bool`        | Flipped fail→pass on clean re-run (flaky)                        |
| `timed_out`       | `bool`        | True if the timeout expired                                      |
| `transcript_path` | `str`         | Full output path (written to `<run_dir>/merge/<tag>-<name>.log`) |

### `ProofBundle`

A patch's full proof is a `ProofBundle`:

- `bundle.green` — `True` iff at least one check was configured and all
  non-quarantined checks passed. Vacuously `False` if receipts is empty.
- `bundle.quarantined` — list of `ProofReceipt` objects that flaked.
- `bundle.failed` — list of `ProofReceipt` objects that failed both runs.

`bundle.green` is the merge precondition. A patch only ships if `bundle.green`
is `True` or no checks were configured.

### Flake quarantine semantics

A check is quarantined when it fails on the first run and passes on a clean
re-run. The semantics are intentionally conservative:

- Quarantined checks **do not block** the patch.
- Quarantined checks **do not count as passing**. A patch with only quarantined
  checks (no real pass, no real fail) enters `quarantined_block` status.
- A quarantined check is never removed from the receipt; it is reported in
  the merge-report and WAL for observability.
- If the bundle has at least one real pass and zero real failures, it is green
  regardless of how many quarantined checks are present.

This means a consistently flaky check never randomly blocks good work, and
never randomly ships bad work.

### Post-merge canary (`canary`)

`canary` is the same format as `checks`. It runs against the **target branch**
after the integration branch is merged in. A canary failure triggers the
auto-revert tripwire (see Guard 3 below).

```python
canary=[{"name": "smoke", "command": ["pytest", "tests/smoke/", "-q"], "timeout": 300}]
```

The canary is distinct from `checks`: `checks` gates individual patches on the
integration branch; `canary` gates the batch as a whole on the real target.

## The three guards

### Guard 1 — test-tamper

A patch that **modifies existing test files** (has real line removals in a file
matching `tests/`, `test_*.py`, `*_test.py`, etc.) cannot self-certify — it
could be weakening the assertions that were supposed to catch it.

Such a patch is routed to `reviewer_agent` as a `wf.code` leaf. The reviewer
sees the diff and decides: `{"accepted": true|false, "reason": "..."}`.

- If the reviewer accepts: the patch proceeds to the apply + proof loop normally.
- If the reviewer rejects (or is unavailable): status is `quarantined_block`.

**What counts as tamper**: any file matching the test globs that has at least
one `-` line that is a real removal (not the `---` diff header). Context-only
changes (`-` lines that are actually diff context) are not counted.

**What is not tamper**: new test files (`new file mode` in the diff). Adding
test coverage is never flagged.

Status on block: `quarantined_block`, reason contains the flagged file list.

### Guard 2 — flake quarantine

Described in detail above under "Flake quarantine semantics". Summary: a check
that flips between runs is neither pass nor fail — it is quarantined and
reported. A patch with only quarantined checks (and no real passes) ends up
`quarantined_block`.

### Guard 3 — auto-revert tripwire

When `auto_merge=True` and the repo is allowlisted:

1. Record `target_sha_before` (the pre-merge HEAD).
2. Fast-merge the integration branch into `target_branch`.
3. Run the `canary` checks against the merged target.
4. If the canary is not green: `git reset --hard <target_sha_before>`, set
   `reverted=True`, and change every previously-`merged` outcome to `exiled`
   with reason `"auto-reverted: post-merge canary failed"`.

If the canary passes, `merged_to_target=True` and the target branch is updated.

The tripwire exists because individual patch checks run on the integration
branch; the canary catches regressions that only appear when the full set
lands together on the real branch.

## The money fence

`auto_merge=True` is not sufficient on its own to promote the integration
branch to the target. The repo must also appear in `config.merge.allowlist`.

**Allowlist matching** is a directory-prefix check on the resolved absolute
repo path:

```yaml
# .flow.yaml
merge:
  allowlist:
    - /home/user/projects/myapp
    - /home/user/projects/lib
```

A repo at `/home/user/projects/myapp` matches the first entry. A repo at
`/home/user/projects/myapp-old` does not (prefix match requires a `/` separator
or exact equality).

**Fail-closed**: if `auto_merge=True` but the repo is not allowlisted, the
integration branch is built normally (all passing patches are committed onto
it), but the branch is **not** promoted to the target. Instead, a
`merge_promotion_withheld` WAL event is emitted with the integration branch
name. The integration branch is available for one-tap human review:

```bash
git diff main..flow/integ-<run_id>   # review what flow would have shipped
git merge --no-ff flow/integ-<run_id>  # approve and promote manually
```

If the repo is not allowlisted and `auto_merge=False`, this is not an error —
the integration branch is built and the caller decides what to do with it.

**Accepted risk in v4**: the allowlist is a repo-root prefix check. There is
no per-path or protected-glob guard (no `"never touch src/payments/"` within
an allowlisted repo). If you need sub-path protection, enforce it in your
`checks` or `canary` commands, or do not allowlist the repo.

## The repair loop

When a patch fails to apply (conflict) or fails the proof checks (red), the
engine runs a bounded repair loop before exiling the patch. `max_repairs`
(default `1`) caps the total number of repair attempts per patch across both
failure modes — conflict repairs and red repairs share the same counter.

**Conflict repair**: the repair agent (`repair_agent`) is given the conflict
detail, the original patch, and the repo at its current HEAD. It is asked to
re-implement the patch's intent against the current tree and stage its edits.
The agent runs as a `wf.code` leaf with `isolation="worktree"`. The produced
diff is read back as the new patch text and re-entered at the top of the apply
loop.

**Red repair**: same pattern. The agent is given the failed check output and
told to fix the code without editing tests.

Both repair types produce a new patch text. If the repair agent returns nothing
(or the worktree produces no diff), the repair is treated as failed and the
budget is not wasted on a retry.

After `max_repairs` attempts, the patch is exiled with a reason describing the
last failure.

## WAL events

`wf.merge` emits the following events to the run journal:

| Event                      | When                                             |
| -------------------------- | ------------------------------------------------ |
| `merge_started`            | Before the first patch is processed              |
| `merge_task_merged`        | A patch was applied, proofs passed, committed    |
| `merge_task_exiled`        | A patch was exiled (conflict, red, empty)        |
| `merge_task_blocked`       | A patch was blocked by guard 1 or guard 2        |
| `merge_promotion_withheld` | `auto_merge=True` but repo not allowlisted       |
| `merge_reverted`           | Auto-revert tripwire fired                       |
| `merge_finished`           | After all patches are processed (always emitted) |

Events are visible in `flow trace <run_id>` and in the raw WAL at
`<run_dir>/journal.jsonl`.

## `merge-report.json`

After every `wf.merge` call, a `merge-report.json` is written to
`<run_dir>/merge-report.json`. It contains the full `MergeResult.as_dict()`
output: all task outcomes with proof receipts, the canary result, and the
target SHA before and after.

```bash
cat $(flow runs --last 1 --dir)/merge-report.json | python3 -m json.tool
```

## Zero-touch ship rate

The canonical metric from `examples/v4_autonomous_backlog.py`:

```python
shipped = len(result["merged"])
total = len(backlog)
zero_touch_ship_rate = round(shipped / total, 3) if total else 0.0
```

This is the fraction of backlog tasks that landed merged + suite-green +
unreverted with no human in the loop. A rate of 1.0 means every task shipped
autonomously. A rate of 0.0 with `merged_to_target=False` but no `reverted`
means the integration branch is built and withheld (likely: repo not
allowlisted, or all patches conflicted).

## Safety model

### Why auto-merge to the main branch is gated on the allowlist

Autonomous merge to a protected branch is an irreversible operation. The
allowlist is an explicit, config-file opt-in — not an env var, not a flag, not
inferred from the repo name. A workflow that sets `auto_merge=True` on an
unallowlisted repo builds the integration branch, emits `merge_promotion_withheld`,
and stops there. This ensures the default behavior for any new repo is
"build and withhold" rather than "build and ship."

The allowlist is fail-closed by design: any error in path resolution causes
the repo to be treated as not allowlisted.

### Accepted risk in v4

Two limitations are honest and documented:

1. **No per-path protection within an allowlisted repo.** The allowlist is an
   all-or-nothing gate at the repo root. Patches that touch `src/payments/` and
   patches that touch `README.md` are treated the same once the repo is
   allowlisted. Enforcement of sub-path restrictions must happen in the `checks`
   or `canary` commands (e.g., a check that fails if certain files are modified).

2. **No engine-level fleet spend floor.** `wf.merge` uses the run's existing
   budget (`wf.spend()` / `max_usd` / `max_tokens`). There is no per-merge
   minimum spend assertion. A repair agent may exhaust the budget mid-loop,
   causing subsequent patches to exile cleanly (budget-exceeded leaves return
   `None`). Monitor `wf.remaining()` if per-merge budget floors matter.
