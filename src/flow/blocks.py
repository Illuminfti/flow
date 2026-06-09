"""Artifact-ref dataflow — shared-context block store + ref substitution
(plan §6 Lever 1/2, WS-C).

A shared context block is sha256-addressed and stored once per run under
``artifacts/blocks/<sha>.txt``. Sibling leaves embed a ref envelope (handle +
short summary + on-disk path) instead of the full blob, so fan-out width no
longer multiplies the shared context's token cost. The engine charges each
block to the budget once per iteration scope, not once per sibling — directly
attacking the cumulative-sum breach at ``budget.py`` ``reserve``.

On-demand resolution: the envelope carries the block's absolute path (CLI-agent
backends read it natively); in-process consumers call :meth:`BlockStore.resolve`
to expand envelopes back to full content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from .backends.pricing import estimate_tokens

SUMMARY_CHARS = 240

_REF = re.compile(r"\[\[flow-block sha256=([0-9a-f]{64})")
_ENVELOPE = re.compile(
    r"\[\[flow-block sha256=([0-9a-f]{64})[^\]]*\]\]\n.*?\n\[\[/flow-block\]\]",
    re.DOTALL)
_SCOPE = re.compile(r"(loop:[^/]+/i\d+)")


def refs_in(text: str) -> list[str]:
    """Ordered, deduped block shas referenced by a prompt."""
    seen: list[str] = []
    for sha in _REF.findall(text or ""):
        if sha not in seen:
            seen.append(sha)
    return seen


def block_scope(phase: str) -> str:
    """Charge scope: the innermost loop iteration when inside wf.loop
    (step + verify share it), else the whole run."""
    scopes = _SCOPE.findall(phase or "")
    return scopes[-1] if scopes else "run"


class BlockStore:
    """Per-run content-addressed block store under ``artifacts/blocks/``.
    The directory is created lazily so v1 run dirs stay byte-identical."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.dir = self.run_dir / "artifacts" / "blocks"
        self._lock = threading.Lock()

    def path_for(self, sha: str) -> Path:
        return self.dir / f"{sha}.txt"

    def put(self, text: str, *, summary_chars: int = SUMMARY_CHARS) -> dict:
        """Store once (idempotent by content hash); return the ref descriptor."""
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path = self.path_for(sha)
        with self._lock:
            if not path.exists():
                self.dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)
        summary = " ".join(text[:summary_chars].split())
        ref = (f"[[flow-block sha256={sha} chars={len(text)} path={path}]]\n"
               f"{summary}\n[[/flow-block]]")
        return {"sha256": sha, "char_count": len(text),
                "tokens_est": estimate_tokens(text),
                "path": str(path.relative_to(self.run_dir)), "ref": ref}

    def read(self, sha: str) -> str:
        return self.path_for(sha).read_text(encoding="utf-8")

    def resolve(self, text: str) -> str:
        """Expand every block envelope back to the full stored content."""
        return _ENVELOPE.sub(lambda m: self.read(m.group(1)), text or "")


def dedup_report(run_dir: Path) -> dict:
    """Lever-1 measurement: shared-context dedup rate from persisted per-leaf
    input hashes + journaled block charges — measured, not inferred.

    ``would_be`` is what v1 would have ingested (every referencing sibling pays
    the full block); ``charged`` is what v2 actually committed (once per
    iteration scope). ``dedup_rate = 1 - charged / would_be``.
    """
    run_dir = Path(run_dir)
    leaves = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted((run_dir / "leaves").glob("*.json"))]
    blocks: dict[str, dict] = {}
    for leaf in leaves:
        for sha in leaf.get("block_refs") or []:
            b = blocks.setdefault(sha, {"ref_count": 0, "charge_count": 0, "scopes": []})
            b["ref_count"] += 1
    journal = run_dir / "journal.jsonl"
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("event") != "block_charged":
                continue
            b = blocks.setdefault(rec["sha256"], {"ref_count": 0, "charge_count": 0, "scopes": []})
            b["charge_count"] += 1
            b["scopes"].append(rec["scope"])
    store = BlockStore(run_dir)
    would_be = charged = 0
    for sha, b in blocks.items():
        tokens = estimate_tokens(store.read(sha))
        b["tokens"] = tokens
        would_be += b["ref_count"] * tokens
        charged += b["charge_count"] * tokens
    return {
        "blocks": blocks,
        "shared_tokens_would_be": would_be,
        "shared_tokens_charged": charged,
        "dedup_rate": 0.0 if not would_be else round(1 - charged / would_be, 4),
        "leaf_count": len(leaves),
        "leaves_with_input_hash": sum(1 for l in leaves if l.get("input_sha256")),
    }
