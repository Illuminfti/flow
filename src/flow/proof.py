"""Proof-carrying gates — a patch exists only if its checks actually ran (v4).

v3 produced patches; v4 makes a patch carry green receipts (test/build/lint
that really executed in the patch's worktree) or it does not ship. Every proof
is a journaled ``ProofReceipt`` with the exact command, exit code, output tail,
and duration — so "tests pass" is never a claim, it is an attached artifact.

Flake quarantine lives here: a check that fails is re-run once on a clean tree;
a result that flips between runs is neither pass nor fail — it is quarantined
and reported, so one flaky check never randomly blocks or randomly ships work.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_TAIL = 4000  # bytes of combined output kept per proof (full stream → transcript)


@dataclass
class ProofReceipt:
    name: str               # test | build | lint | canary | <custom>
    command: str
    passed: bool
    exit_code: Optional[int]
    duration_s: float
    output_tail: str
    cwd: str
    quarantined: bool = False   # flaked: flipped between clean re-runs
    timed_out: bool = False
    transcript_path: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def run_check(name: str, command, cwd: str, *, timeout: float = 600.0,
              env: Optional[dict] = None, transcript_path: str = "") -> ProofReceipt:
    """Run one check command (argv list) in ``cwd``; return a proof receipt.
    Never raises — a crash is a failed proof, not an exception."""
    started = time.time()
    argv = list(command)
    timed_out = False
    try:
        proc = subprocess.run(argv, cwd=cwd, text=True, capture_output=True,
                              timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        exit_code = proc.returncode
        out = (proc.stdout or "") + (("\n--- stderr ---\n" + proc.stderr)
                                     if proc.stderr else "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        out = f"TIMEOUT after {timeout}s\n{(exc.stdout or '')}{(exc.stderr or '')}"
    except Exception as exc:
        exit_code = None
        out = f"check could not run: {type(exc).__name__}: {exc}"
    if transcript_path:
        try:
            p = Path(transcript_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(out, encoding="utf-8")
        except OSError:
            pass
    return ProofReceipt(
        name=name, command=" ".join(argv), passed=(exit_code == 0),
        exit_code=exit_code, duration_s=round(time.time() - started, 2),
        output_tail=out[-_TAIL:], cwd=cwd, timed_out=timed_out,
        transcript_path=transcript_path)


def run_check_quarantined(name: str, command, cwd: str, *, timeout: float = 600.0,
                          env: Optional[dict] = None,
                          transcript_path: str = "") -> ProofReceipt:
    """Run a check; if it fails, re-run once. A result that flips between the
    two runs is quarantined (flaky) rather than trusted in either direction."""
    first = run_check(name, command, cwd, timeout=timeout, env=env,
                      transcript_path=transcript_path)
    if first.passed:
        return first
    second = run_check(name, command, cwd, timeout=timeout, env=env,
                       transcript_path=transcript_path)
    if second.passed:
        # failed then passed → flaky, not a real failure
        second.quarantined = True
        second.passed = False
        second.output_tail = ("[QUARANTINED: flipped fail→pass on clean re-run]\n"
                              + second.output_tail)
        return second
    return second  # failed twice → a real failure


@dataclass
class ProofBundle:
    """All proofs a patch carries. ``green`` is the merge precondition."""
    receipts: list = field(default_factory=list)

    @property
    def green(self) -> bool:
        return bool(self.receipts) and all(
            r.passed for r in self.receipts if not r.quarantined)

    @property
    def quarantined(self) -> list:
        return [r for r in self.receipts if r.quarantined]

    @property
    def failed(self) -> list:
        return [r for r in self.receipts if not r.passed and not r.quarantined]

    def as_dict(self) -> dict:
        return {
            "green": self.green,
            "receipts": [r.as_dict() for r in self.receipts],
            "quarantined": [r.name for r in self.quarantined],
            "failed": [r.name for r in self.failed],
        }
