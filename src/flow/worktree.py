"""Repo isolation for parallel agent leaves (v3/v4).

N agent leaves mutating one repo concurrently would trample each other. With
``isolation="worktree"`` each leaf gets its OWN isolated checkout of the repo;
after the leaf finishes, its full change set (committed *and* uncommitted, vs
the commit it started from) is captured as a patch artifact and the checkout is
removed. The caller decides what to apply — flow reports evidence, it never
merges into the live repo.

Isolation is a **local clone**, not a linked ``git worktree``. A coding-agent
sandbox (Codex) resolves its writable root through the shared ``.git`` of a
linked worktree and writes to the *main* working tree — so four parallel agents
clobber each other in the source repo and every linked worktree stays empty
(the v4 kill-run bug). An independent clone has its own ``.git``; the agent's
repo-root resolution lands inside it, and parallel clones cannot collide.
``git clone --local`` hardlinks the object store, so this is cheap.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

# Clone/remove touch the source .git briefly — serialize to be safe under fan-out.
_GIT_LOCK = threading.Lock()

_NOISE = (".pyc", ".pyo")
_NOISE_DIRS = ("__pycache__/", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/", ".git/")


class WorktreeError(RuntimeError):
    pass


def _git(repo: Path, *args: str, timeout: float = 180.0, check: bool = True):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          text=True, capture_output=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args[:2])}: {(proc.stderr or proc.stdout)[:400]}")
    return proc.stdout


def repo_root(path: str) -> Optional[Path]:
    try:
        proc = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                              text=True, capture_output=True, timeout=30)
        return Path(proc.stdout.strip()) if proc.returncode == 0 else None
    except Exception:
        return None


def prepare(workspace: str, dest: Path) -> tuple[Path, str]:
    """Make an isolated local clone of the repo at ``workspace``, checked out to
    its current HEAD. Returns (clone_path, base_sha) — base_sha is the commit
    the clone starts from, the reference ``finalize`` diffs against."""
    root = repo_root(workspace)
    if root is None:
        raise WorktreeError(f"worktree isolation needs a git repo at {workspace}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    with _GIT_LOCK:
        base_sha = _git(root, "rev-parse", "HEAD").strip()
        # --local: hardlink the object store (cheap); the clone is fully
        # independent (own .git/HEAD/index), so an agent sandbox writes inside it.
        subprocess.run(["git", "clone", "--local", "--quiet", str(root), str(dest)],
                       text=True, capture_output=True, timeout=300, check=True)
    _git(dest, "checkout", "--quiet", "--detach", base_sha)
    # carry the source's committer identity so the agent can commit if it wants
    _git(dest, "config", "user.email", _git(root, "config", "user.email", check=False).strip() or "flow@local")
    _git(dest, "config", "user.name", _git(root, "config", "user.name", check=False).strip() or "flow")
    return dest, base_sha


def _is_noise(path: str) -> bool:
    return path.endswith(_NOISE) or any(d in path for d in _NOISE_DIRS)


def finalize(workspace: str, wt: Path, patch_path: Path, *, base_sha: str = "") -> dict:
    """Capture the clone's full change set vs ``base_sha`` (committed +
    uncommitted, minus build noise) as a patch artifact, then remove the clone.
    Returns {"changed", "patch", "files", "error"}."""
    if not wt.exists():
        return {"changed": False, "patch": "", "files": [], "error": "clone missing"}
    error = ""
    diff, files = "", []
    try:
        _git(wt, "add", "-A")
        ref = base_sha or "HEAD"
        all_files = [f for f in _git(wt, "diff", "--cached", "--name-only", ref)
                     .splitlines() if f.strip()]
        files = [f for f in all_files if not _is_noise(f)]
        if files:
            diff = _git(wt, "diff", "--cached", "--binary", ref, "--", *files)
    except WorktreeError as exc:
        error = str(exc)
    changed = bool(diff.strip())
    if changed:
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff, encoding="utf-8")
    shutil.rmtree(wt, ignore_errors=True)
    return {"changed": changed, "patch": str(patch_path) if changed else "",
            "files": files, "error": error}
