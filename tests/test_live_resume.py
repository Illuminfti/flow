"""Phase 3C: external SIGKILL mid-run, then resume — the real crash-resume proof."""
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from flow.runtime import run_workflow
from tests import _kill_subprocess as ks


def test_external_sigkill_then_resume(tmp_path):
    data = tmp_path / "data"
    sentinel = tmp_path / "s.txt"
    sentinel.write_text("")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests._kill_subprocess", str(data), str(sentinel)],
        cwd=str(root), env={**os.environ, "PYTHONPATH": str(root / "src"), "FLOW_DATA_DIR": str(data)},
    )
    # wait until at least 2 leaves have started, then SIGKILL mid-flight
    deadline = time.time() + 15
    while time.time() < deadline:
        n = len([x for x in sentinel.read_text().split() if x])
        if n >= 2:
            break
        time.sleep(0.05)
    time.sleep(0.1)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    assert proc.returncode in (-9, 137)

    pre = Counter(int(x) for x in sentinel.read_text().split())
    started = sorted(pre)
    assert len(started) >= 2

    # resume in-process
    os.environ["FLOW_DATA_DIR"] = str(data)
    try:
        rep = run_workflow(run_fn=ks.build_run(str(sentinel)), run_id=ks.RUN_ID,
                           script_id=ks.SCRIPT_ID, slug="kill", max_workers=1)
        assert rep["status"] == "completed"
        assert rep["final"] == list(range(8))
        post = Counter(int(x) for x in sentinel.read_text().split())
        # leaves that COMPLETED before the kill (all started except possibly the
        # last in-flight one) are not re-run; the interrupted one + the rest run.
        # The earliest started leaves definitely completed (sequential, 0.5s each).
        assert post[started[0]] == 1, f"completed leaf {started[0]} was re-run"
        assert rep["resumed"] >= 1
        # every leaf ends present in the final run
        assert all(post[i] >= 1 for i in range(8))
    finally:
        os.environ.pop("FLOW_DATA_DIR", None)
