"""Merge orchestrator — proof-gated rebase queue that ships outcomes (v4).

v3 ended with loose patches a human had to integrate. v4 takes the patch set
from a fan-out of worktree-isolated agent leaves and drives it to merged,
proven code on a target branch — sequentially, each patch gated by real proof
receipts (proof.py), with three guards that make autonomous merge safe:

  1. test-tamper gate   — a patch that *modifies* existing tests can never
                          self-certify against them; it is routed to a
                          cross-vendor reviewer leaf and fails closed if not
                          explicitly accepted. New tests are fine.
  2. flake quarantine   — proof checks re-run on flip (proof.py); a flaky
                          check neither blocks nor ships.
  3. auto-revert tripwire — auto-merge to the protected branch records a revert
                          SHA and runs a post-merge canary; a canary failure
                          reverts the merge and exiles the batch.

Money fence: auto-merge to a protected branch is allowed only when the repo
matches the config ``merge.allowlist`` — fail-closed, like verifier identity.
Flow owns the integration branch; promotion of a non-allowlisted repo to its
protected branch is exiled to the handoff for one-tap human approval.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import proof as proof_mod

_TEST_GLOBS = (re.compile(r"(^|/)tests?/"), re.compile(r"(^|/)test_[^/]*\.py$"),
               re.compile(r"_test\.(py|js|ts|go|rs)$"),
               re.compile(r"\.(test|spec)\.(js|ts|jsx|tsx)$"))


class MergeRefused(RuntimeError):
    """Auto-merge to a protected branch on a non-allowlisted repo (money fence)."""


def _git(repo, *args: str, timeout: float = 180.0, check: bool = True):
    proc = subprocess.run(["git", "-C", str(repo), *args], text=True,
                          capture_output=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(proc.stderr or proc.stdout)[:400]}")
    return proc


def repo_root(path: str) -> Optional[Path]:
    try:
        r = _git(path, "rev-parse", "--show-toplevel", check=False)
        return Path(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        return None


def head_sha(repo) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def current_branch(repo) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


# -- patch inspection -------------------------------------------------------

def patch_files(patch_text: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", patch_text, re.M)


def _new_files_by_section(patch_text: str) -> set[str]:
    out: set[str] = set()
    section_new = False
    path = None
    for line in patch_text.splitlines():
        if line.startswith("diff --git"):
            section_new = False
            path = None
        elif line.startswith("new file mode"):
            section_new = True
        elif line.startswith("+++ b/"):
            path = line[6:]
            if section_new and path:
                out.add(path)
    return out


def is_test_path(path: str) -> bool:
    return any(g.search(path) for g in _TEST_GLOBS)


def touches_existing_tests(patch_text: str) -> list[str]:
    """Files that are existing test files modified by this patch (a `-` line
    that is a real removal, not a moved add). New test files are excluded —
    they are not tamper, they are coverage."""
    new = _new_files_by_section(patch_text)
    flagged: list[str] = []
    cur = None
    has_removal = False
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            if cur and is_test_path(cur) and cur not in new and has_removal:
                flagged.append(cur)
            cur = line[6:]
            has_removal = False
        elif line.startswith("-") and not line.startswith("---"):
            has_removal = True
    if cur and is_test_path(cur) and cur not in new and has_removal:
        flagged.append(cur)
    return sorted(set(flagged))


# -- apply ------------------------------------------------------------------

def apply_patch(repo, patch_text: str) -> tuple[bool, str]:
    """3-way apply a patch onto the working tree + index. Returns (ok, detail).
    ok=False on a conflict the 3-way merge could not resolve."""
    proc = subprocess.run(["git", "-C", str(repo), "apply", "--3way", "--index", "-"],
                          input=patch_text, text=True, capture_output=True, timeout=120)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout)[:600]


# -- result types -----------------------------------------------------------

@dataclass
class TaskOutcome:
    task_id: str
    status: str            # merged | exiled | quarantined_block
    reason: str = ""
    files: list = field(default_factory=list)
    proofs: dict = field(default_factory=dict)
    commit: str = ""
    review: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class MergeResult:
    repo: str
    target_branch: str
    integration_branch: str
    merged_to_target: bool
    target_sha_before: str
    target_sha_after: str
    reverted: bool
    outcomes: list
    canary: dict = field(default_factory=dict)

    @property
    def merged(self) -> list:
        return [o for o in self.outcomes if o.status == "merged"]

    @property
    def exiled(self) -> list:
        return [o for o in self.outcomes if o.status != "merged"]

    def as_dict(self) -> dict:
        return {
            "repo": self.repo, "target_branch": self.target_branch,
            "integration_branch": self.integration_branch,
            "merged_to_target": self.merged_to_target,
            "target_sha_before": self.target_sha_before,
            "target_sha_after": self.target_sha_after, "reverted": self.reverted,
            "merged": [o.task_id for o in self.merged],
            "exiled": [o.task_id for o in self.exiled],
            "outcomes": [o.as_dict() for o in self.outcomes],
            "canary": self.canary,
        }


# -- proof gate -------------------------------------------------------------

def _run_proofs(checks: list, cwd: str, *, transcript_dir: Optional[Path] = None,
                tag: str = "") -> proof_mod.ProofBundle:
    bundle = proof_mod.ProofBundle()
    for chk in checks:
        tp = ""
        if transcript_dir is not None:
            tp = str(transcript_dir / f"{tag}-{chk['name']}.log")
        bundle.receipts.append(proof_mod.run_check_quarantined(
            chk["name"], chk["command"], cwd,
            timeout=float(chk.get("timeout", 600.0)), transcript_path=tp))
    return bundle


# -- the orchestrator -------------------------------------------------------

def orchestrate(
    wf: Any,
    *,
    repo: str,
    patches: list,               # [{task_id, patch_text|patch_path, files}]
    config: dict,
    target_branch: Optional[str] = None,
    checks: Optional[list] = None,     # [{name, command, timeout}] proof gate
    canary: Optional[list] = None,     # post-merge checks on the target branch
    auto_merge: bool = False,
    max_repairs: int = 1,
    repair_agent: str = "coder",
    reviewer_agent: str = "reviewer",
) -> MergeResult:
    """Drive a patch set to merged+proven on ``target_branch``. Pure-git steps
    run here; repair/review are agent leaves via ``wf.code`` (budget-gated,
    journaled). See module docstring for the guard semantics."""
    root = repo_root(repo)
    if root is None:
        raise RuntimeError(f"merge target is not a git repo: {repo}")
    root = str(root)
    target = target_branch or current_branch(root)
    merge_cfg = config.get("merge") or {}
    checks = checks or merge_cfg.get("checks") or []
    canary = canary if canary is not None else (merge_cfg.get("canary") or [])

    # Money fence (fail-closed): auto-merge to the target only on an allowlisted repo.
    allowlisted = _allowlisted(root, merge_cfg.get("allowlist") or [])
    do_auto = bool(auto_merge and allowlisted)

    run_dir = Path(wf._engine.run_dir)
    tdir = run_dir / "merge"
    tdir.mkdir(parents=True, exist_ok=True)

    target_before = head_sha(root)
    integ = f"flow/integ-{run_dir.name}"
    _git(root, "checkout", "-q", "-B", integ, target)
    _emit(wf, "merge_started", repo=root, target=target, integration=integ,
          patches=len(patches), auto_merge=do_auto, allowlisted=allowlisted)

    outcomes: list[TaskOutcome] = []
    for spec in patches:
        outcomes.append(_apply_one(
            wf, root=root, integ=integ, spec=spec, checks=checks, tdir=tdir,
            max_repairs=max_repairs, repair_agent=repair_agent,
            reviewer_agent=reviewer_agent))

    merged_any = any(o.status == "merged" for o in outcomes)
    do_promote = do_auto and merged_any
    reverted = False
    canary_result: dict = {}
    target_after = target_before

    if do_promote:
        _git(root, "checkout", "-q", target)
        _git(root, "merge", "--no-ff", "-q", "-m",
             f"flow: merge {sum(o.status=='merged' for o in outcomes)} task(s) via {integ}", integ)
        target_after = head_sha(root)
        if canary:
            bundle = _run_proofs(canary, root, transcript_dir=tdir, tag="canary")
            canary_result = bundle.as_dict()
            if not bundle.green:
                # auto-revert tripwire: roll the target back to its pre-merge SHA
                _git(root, "reset", "--hard", "-q", target_before)
                target_after = target_before
                reverted = True
                for o in outcomes:
                    if o.status == "merged":
                        o.status = "exiled"
                        o.reason = "auto-reverted: post-merge canary failed"
                _emit(wf, "merge_reverted", repo=root, target=target,
                      to_sha=target_before, canary=canary_result)
    elif auto_merge and not allowlisted:
        _emit(wf, "merge_promotion_withheld", repo=root, target=target,
              reason="repo not in merge.allowlist — exiled for one-tap approval",
              integration=integ)

    result = MergeResult(
        repo=root, target_branch=target, integration_branch=integ,
        merged_to_target=(do_promote and not reverted),
        target_sha_before=target_before, target_sha_after=target_after,
        reverted=reverted, outcomes=outcomes, canary=canary_result)
    (run_dir / "merge-report.json").write_text(
        __import__("json").dumps(result.as_dict(), indent=2), encoding="utf-8")
    _emit(wf, "merge_finished", **{k: result.as_dict()[k] for k in
          ("merged", "exiled", "merged_to_target", "reverted")})
    return result


def _apply_one(wf, *, root, integ, spec, checks, tdir, max_repairs,
               repair_agent, reviewer_agent) -> TaskOutcome:
    task_id = spec.get("task_id") or spec.get("label") or "task"
    patch_text = _patch_text(spec)
    files = spec.get("files") or patch_files(patch_text)
    out = TaskOutcome(task_id=task_id, status="exiled", files=files)
    if not patch_text.strip():
        out.reason = "empty patch"
        return out

    # Guard 1 — test-tamper: a patch modifying existing tests must pass review.
    tampered = touches_existing_tests(patch_text)
    if tampered:
        review = _review_test_change(wf, root, task_id, patch_text, tampered, reviewer_agent)
        out.review = review
        if not review.get("accepted"):
            out.status = "quarantined_block"
            out.reason = f"test-tamper not accepted by reviewer: {tampered}"
            _emit(wf, "merge_task_blocked", task=task_id, reason=out.reason)
            return out

    # Apply (with bounded repair on conflict), then proof-gate (with bounded
    # repair on red). Both share the same repair budget.
    repairs = 0
    while True:
        ok, detail = apply_patch(root, patch_text)
        if not ok:
            # a failed 3-way apply can leave an unmerged index; hard-reset
            # back to the integration HEAD before any retry.
            _git(root, "reset", "--hard", "-q")
            _git(root, "clean", "-fdq", check=False)
            if repairs < max_repairs:
                repairs += 1
                patch_text = _repair_conflict(wf, root, task_id, patch_text, detail, repair_agent)
                if patch_text:
                    continue
            out.reason = f"apply conflict unresolved: {detail[:200]}"
            _emit(wf, "merge_task_exiled", task=task_id, reason=out.reason)
            return out
        bundle = _run_proofs(checks, root, transcript_dir=tdir, tag=task_id)
        out.proofs = bundle.as_dict()
        if bundle.green or not checks:
            _git(root, "commit", "-q", "-m", f"flow[{task_id}]: {', '.join(files[:3])}")
            out.status = "merged"
            out.commit = head_sha(root)
            out.reason = "applied + proofs green" if checks else "applied (no checks configured)"
            _emit(wf, "merge_task_merged", task=task_id, commit=out.commit,
                  proofs=out.proofs.get("green"))
            return out
        # red → drop the applied changes, try a bounded repair
        _git(root, "reset", "--hard", "-q")
        if bundle.quarantined and not bundle.failed:
            out.status = "quarantined_block"
            out.reason = f"checks quarantined (flaky): {[r.name for r in bundle.quarantined]}"
            _emit(wf, "merge_task_blocked", task=task_id, reason=out.reason)
            return out
        if repairs < max_repairs:
            repairs += 1
            patch_text = _repair_red(wf, root, task_id, patch_text, bundle, repair_agent)
            if patch_text:
                continue
        out.reason = f"proofs red after {repairs} repair(s): {[r.name for r in bundle.failed]}"
        _emit(wf, "merge_task_exiled", task=task_id, reason=out.reason)
        return out


# -- agent-leaf steps (the only model work) ---------------------------------

_REVIEW_SCHEMA = {"type": "object", "required": ["accepted", "reason"],
                  "properties": {"accepted": {"type": "boolean"},
                                 "reason": {"type": "string"}},
                  "additionalProperties": False}


def _review_test_change(wf, root, task_id, patch_text, files, agent) -> dict:
    r = wf.code(
        f"A patch for task {task_id!r} MODIFIES existing test files: {files}. "
        "Existing tests are the contract; weakening or deleting assertions to make "
        "code pass is forbidden, but a legitimate test update (renamed API, corrected "
        "expectation, added coverage) is fine. Review this diff and decide.\n\n"
        f"```diff\n{patch_text[:6000]}\n```\n\n"
        'Reply ONLY JSON {"accepted": true|false, "reason": "..."}.',
        agent=agent, workspace=root, schema=_REVIEW_SCHEMA,
        label=f"merge-review:{task_id}", required=False)
    if not r or not r.get("value"):
        return {"accepted": False, "reason": "reviewer unavailable or returned nothing"}
    return {"accepted": bool(r["value"]["accepted"]), "reason": r["value"]["reason"],
            "agent": agent}


def _repair_conflict(wf, root, task_id, patch_text, detail, agent) -> str:
    return _repair(wf, root, task_id, agent, label=f"merge-repair-conflict:{task_id}",
                   prompt=(
        f"Applying this patch for task {task_id!r} onto the current tree FAILED to "
        f"merge:\n{detail}\n\nThe repo is at {root}. Re-create the patch's INTENT "
        "against the current code (read the conflicting files, apply the change so it "
        "fits), staging your edits. Do not weaken tests. When done, reply JSON "
        '{"done": true}.\n\nOriginal patch:\n```diff\n' + patch_text[:6000] + "\n```"))


def _repair_red(wf, root, task_id, patch_text, bundle, agent) -> str:
    fails = "\n\n".join(f"[{r.name}] exit={r.exit_code}\n{r.output_tail[-1200:]}"
                        for r in bundle.failed)
    return _repair(wf, root, task_id, agent, label=f"merge-repair-red:{task_id}",
                   prompt=(
        f"This change for task {task_id!r} was applied but the proof checks FAILED:\n"
        f"{fails}\n\nThe repo is at {root}. Fix the code so the checks pass — do NOT "
        "edit the tests to make them pass. Stage your edits. Reply JSON "
        '{"done": true}.'))


def _repair(wf, root, task_id, agent, *, label, prompt) -> str:
    """Run a repair agent in an isolated worktree; return the new patch text it
    produces (empty string if it changed nothing)."""
    r = wf.code(prompt, agent=agent, workspace=root, isolation="worktree",
                label=label, required=False,
                schema={"type": "object", "required": ["done"],
                        "properties": {"done": {"type": "boolean"}},
                        "additionalProperties": False})
    if not r:
        return ""
    patch = (r.get("patch") or {})
    if not patch.get("changed"):
        return ""
    p = patch.get("patch")
    if not p:
        return ""
    full = Path(wf._engine.run_dir) / p
    try:
        return full.read_text(encoding="utf-8")
    except OSError:
        return ""


# -- helpers ----------------------------------------------------------------

def _patch_text(spec: dict) -> str:
    if spec.get("patch_text"):
        return spec["patch_text"]
    p = spec.get("patch_path") or spec.get("patch")
    if p and Path(p).exists():
        return Path(p).read_text(encoding="utf-8")
    return ""


def _allowlisted(root: str, allowlist: list) -> bool:
    root = str(Path(root).resolve())
    for entry in allowlist:
        e = str(Path(entry).expanduser())
        try:
            e = str(Path(e).resolve())
        except OSError:
            pass
        if root == e or root.startswith(e.rstrip("/") + "/"):
            return True
    return False


def _emit(wf, event: str, **fields) -> None:
    try:
        wf._engine._emit({"event": event, **fields})
    except Exception:
        pass


def find_patches_in_run(wf, receipts: list) -> list:
    """Convenience: turn a list of wf.code receipts into merge patch specs."""
    run_dir = Path(wf._engine.run_dir)
    out = []
    for r in receipts:
        if not r:
            continue
        patch = (r.get("patch") or {})
        if not patch.get("changed"):
            continue
        out.append({"task_id": r.get("leaf_id", "task")[:16],
                    "patch_path": str(run_dir / patch["patch"]),
                    "files": patch.get("files") or []})
    return out
