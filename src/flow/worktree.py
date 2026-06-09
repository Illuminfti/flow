"""Git-worktree isolation for parallel agent leaves (v3).

N agent leaves mutating one repo concurrently would trample each other. With
``isolation="worktree"`` each leaf gets a detached worktree of the repo; after
the leaf finishes, its changes are captured as a patch artifact and the
worktree is removed (clean worktrees leave nothing behind). The caller decides
what to apply — flow reports evidence, it does not merge.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Optional

# git mutates .git/worktrees metadata on add/remove — serialize within-process.
_GIT_LOCK = threading.Lock()


class WorktreeError(RuntimeError):
    pass


def _git(repo: Path, *args: str, timeout: float = 120.0) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args[:2])}: {(proc.stderr or proc.stdout)[:400]}")
    return proc.stdout


def repo_root(path: str) -> Optional[Path]:
    try:
        out = _git(Path(path), "rev-parse", "--show-toplevel")
        return Path(out.strip())
    except Exception:
        return None


def prepare(workspace: str, dest: Path) -> Path:
    """Create a detached worktree of the repo containing ``workspace``.
    Returns the worktree path the agent should run in."""
    root = repo_root(workspace)
    if root is None:
        raise WorktreeError(f"worktree isolation needs a git repo at {workspace}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _GIT_LOCK:
        _git(root, "worktree", "add", "--detach", str(dest))
    return dest


def finalize(workspace: str, wt: Path, patch_path: Path) -> dict:
    """Capture the worktree's changes as a patch artifact, then remove it.
    Returns {"changed": bool, "patch": relpath-or-"", "files": [..]}."""
    root = repo_root(workspace)
    if root is None or not wt.exists():
        return {"changed": False, "patch": "", "files": []}
    try:
        _git(wt, "add", "-A")
        diff = _git(wt, "diff", "--cached", "--binary")
        files = [l.strip() for l in _git(wt, "diff", "--cached", "--name-only").splitlines() if l.strip()]
    except Exception:
        diff, files = "", []
    changed = bool(diff.strip())
    if changed:
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff, encoding="utf-8")
    with _GIT_LOCK:
        try:
            _git(root, "worktree", "remove", "--force", str(wt))
        except Exception:
            pass  # leftover worktree is debris, not data loss — prune later
    return {"changed": changed, "patch": str(patch_path) if changed else "", "files": files}
