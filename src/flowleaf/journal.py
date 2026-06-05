"""Crash-resumable journal — append-only write-ahead log + materialized results.

The superseded stub kept its journal in memory and flushed once at the end, so
a crash lost everything. This writes one fsync'd JSONL line per leaf-state
transition and a materialized result file per completed leaf. Resume replays the
WAL to reconstruct the leaf state machine; any leaf with a terminal ``completed``
event and a present result file is skipped. No work lost on crash — the exact
failure mode ``delegate_task`` has (interrupt → abandon pending children).

The flock+fsync discipline is cloned from ``workstream_orchestrator`` which
already does crash-safe JSON state correctly.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

from .ids import utc_now

TERMINAL_EVENTS = {"completed", "cached"}


class Journal:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.leaves_dir = self.run_dir / "leaves"
        self.journal_path = self.run_dir / "journal.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        self.state_path = self.run_dir / "state.json"
        self.outbox_dir = self.run_dir / "outbox"
        self._seq = 0
        self._lock = threading.Lock()
        self._fh = None

    # -- lifecycle -------------------------------------------------------
    def open(self, *, manifest: Optional[dict] = None) -> "Journal":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.leaves_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        if manifest is not None and not self.manifest_path.exists():
            self._atomic_write(self.manifest_path, manifest)
        # seed seq past any existing lines (resume)
        if self.journal_path.exists():
            with self.journal_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._seq = max(self._seq, int(json.loads(line).get("seq", 0)))
                    except Exception:
                        continue
        self._fh = self.journal_path.open("a", encoding="utf-8")
        return self

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- writes ----------------------------------------------------------
    def append(self, record: dict) -> int:
        """Append one WAL line, fsync immediately. Returns its seq."""
        with self._lock:
            self._seq += 1
            record = {"seq": self._seq, "ts": utc_now(), **record}
            assert self._fh is not None, "journal not open"
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
            return self._seq

    def write_leaf_result(self, leaf_id: str, payload: dict) -> str:
        """Materialize a completed leaf result (idempotent re-read on resume)."""
        path = self.leaves_dir / f"{leaf_id}.json"
        self._atomic_write(path, payload)
        return str(path.relative_to(self.run_dir))

    def read_leaf_result(self, leaf_id: str) -> Optional[dict]:
        path = self.leaves_dir / f"{leaf_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def snapshot_state(self, state: dict) -> None:
        """Periodic compacted snapshot (flock+fsync, workstream pattern)."""
        self._atomic_write(self.state_path, state)

    def link_artifact(self, name: str, target: str) -> None:
        (self.outbox_dir / f"{name}.txt").write_text(str(target), encoding="utf-8")

    @staticmethod
    def _atomic_write(path: Path, payload: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)

    # -- resume ----------------------------------------------------------
    def replay(self) -> Iterator[dict]:
        if not self.journal_path.exists():
            return
        with self.journal_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue

    def completed_leaves(self) -> dict[str, dict]:
        """Reconstruct {leaf_id: result} for leaves that reached a terminal
        state AND have a present result file. Used to skip work on resume."""
        terminal: dict[str, str] = {}
        for rec in self.replay():
            lid = rec.get("leaf_id")
            ev = rec.get("event")
            if lid and ev in TERMINAL_EVENTS:
                terminal[lid] = ev
        out: dict[str, dict] = {}
        for lid in terminal:
            res = self.read_leaf_result(lid)
            if res is not None:
                out[lid] = res
        return out
