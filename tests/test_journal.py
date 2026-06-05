import subprocess
import sys
from collections import Counter
from pathlib import Path

from flow.runtime import run_workflow
from tests import _crash_helpers as ch


def test_hard_crash_then_resume(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    sentinel = tmp_path / "sentinel.txt"
    marker = tmp_path / "crashed.marker"

    proc = subprocess.run(
        [sys.executable, "-m", "tests._crash_helpers", str(data_dir), str(sentinel), "2", str(marker)],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert proc.returncode == 137, (proc.returncode, proc.stderr[-500:])

    pre = Counter(int(x) for x in sentinel.read_text().split())
    assert pre[0] == 1 and pre[1] == 1 and pre[2] == 1 and 3 not in pre

    monkeypatch.setenv("FLOW_DATA_DIR", str(data_dir))
    rep = run_workflow(run_fn=ch.build_run(str(sentinel), 2, str(marker)),
                       run_id=ch.RUN_ID, script_id=ch.SCRIPT_ID, slug="crash", max_workers=1)
    assert rep["status"] == "completed"
    assert rep["final"] == [0, 1, 2, 3, 4]

    post = Counter(int(x) for x in sentinel.read_text().split())
    assert post[0] == 1 and post[1] == 1   # journaled, not re-run
    assert post[2] == 2                     # only 'started' pre-crash -> re-run
    assert post[3] == 1 and post[4] == 1
    assert rep["resumed"] == 2
