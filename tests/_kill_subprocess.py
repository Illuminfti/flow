"""Long-running workflow for the external-SIGKILL resume test."""
from __future__ import annotations

import os
import sys
import time

from flow.runtime import run_workflow

RUN_ID = "kill-fixed-001"
SCRIPT_ID = "kill-script"


def slow(idx, sentinel):
    with open(sentinel, "a", encoding="utf-8") as f:
        f.write(f"{idx}\n")
        f.flush()
        os.fsync(f.fileno())
    time.sleep(0.5)
    return idx


def build_run(sentinel, n=8):
    def run(wf, args):
        wf.phase("p")
        return [wf.local(slow, i, sentinel, label=f"k{i}") for i in range(n)]
    return run


def main():
    data, sentinel = sys.argv[1:3]
    os.environ["FLOW_DATA_DIR"] = data
    run_workflow(run_fn=build_run(sentinel), run_id=RUN_ID, script_id=SCRIPT_ID,
                 slug="kill", max_workers=1)


if __name__ == "__main__":
    main()
