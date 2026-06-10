"""v4 kill condition — a backlog goes in, merged+proven commits come out.

Fan out one coding agent per backlog task (each in an isolated git worktree),
then drive every produced patch through ``wf.merge``: proof-gate it against the
repo's real test suite, route test edits to a reviewer, auto-merge the green
set to the target branch, and auto-revert if the post-merge canary fails.

The metric is zero-touch ship rate: tasks that land merged + suite-green +
unreverted with no human in the loop.

    flow run examples/v4_autonomous_backlog.py --args '{
      "repo": "/path/to/repo", "target": "master",
      "test_command": ["python3", "-m", "pytest", "-q"],
      "backlog": [
        {"id": "feat-tags", "prompt": "Add a LinkStore.remove_tag(url, tag) method ..."},
        ...
      ]}' --budget '{"max_tokens": 2000000, "max_calls": 60}'

The repo must be in the config ``merge.allowlist`` for auto-merge to fire;
otherwise the green integration branch is withheld for one-tap approval.
"""

DONE = {"type": "object", "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
        "additionalProperties": False}


def run(wf, args):
    repo = args["repo"]
    target = args.get("target", "main")
    test_cmd = args["test_command"]
    backlog = args["backlog"]

    wf.phase("implement")
    # one isolated coding agent per task — they cannot trample each other
    receipts = wf.parallel([
        (lambda t=t: wf.code(
            f"You are working in the repo at {repo}. Task {t['id']!r}: {t['prompt']}\n\n"
            "Implement it cleanly and add or extend tests to cover it. Do NOT weaken "
            "existing tests. Run the test suite yourself before finishing. "
            'Reply with JSON {"summary": "<what you changed>"}.',
            agent="coder", workspace=repo, isolation="worktree",
            schema=DONE, label=t["id"], required=False))
        for t in backlog
    ])

    # tag each receipt with its backlog id for the merge report
    specs = []
    for t, r in zip(backlog, receipts):
        if r and (r.get("patch") or {}).get("changed"):
            specs.append({"task_id": t["id"],
                          "patch_path": str(wf._engine.run_dir / r["patch"]["patch"]),
                          "files": r["patch"]["files"]})

    wf.phase("ship")
    result = wf.merge(
        specs, repo=repo, target_branch=target,
        checks=[{"name": "suite", "command": test_cmd, "timeout": 900}],
        canary=[{"name": "canary", "command": test_cmd, "timeout": 900}],
        auto_merge=True, max_repairs=1)

    shipped = len(result["merged"])
    total = len(backlog)
    return {
        "zero_touch_ship_rate": round(shipped / total, 3) if total else 0.0,
        "shipped": shipped, "total": total,
        "merged": result["merged"], "exiled": result["exiled"],
        "merged_to_target": result["merged_to_target"],
        "reverted": result["reverted"],
        "produced_patches": len(specs),
    }
