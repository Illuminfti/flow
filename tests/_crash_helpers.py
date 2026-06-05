"""Subprocess helper for the hard-crash resume test."""
from __future__ import annotations

import os
import sys

from flowleaf.runtime import run_workflow

RUN_ID = "crash-fixed-001"
SCRIPT_ID = "crash-script"


def crashy(idx, sentinel_path, crash_at, crash_marker):
    with open(sentinel_path, "a", encoding="utf-8") as f:
        f.write(f"{idx}\n")
        f.flush()
        os.fsync(f.fileno())
    if idx == crash_at and not os.path.exists(crash_marker):
        open(crash_marker, "w").close()
        os._exit(137)
    return idx


def build_run(sentinel_path, crash_at, crash_marker, n=5):
    def run(wf, args):
        wf.phase("crashy")
        return [wf.local(crashy, i, sentinel_path, crash_at, crash_marker, label=f"k{i}") for i in range(n)]
    return run


def main():
    data_dir, sentinel, crash_at, crash_marker = sys.argv[1:5]
    os.environ["FLOWLEAF_DATA_DIR"] = data_dir
    run_workflow(run_fn=build_run(sentinel, int(crash_at), crash_marker),
                 run_id=RUN_ID, script_id=SCRIPT_ID, slug="crash", max_workers=1)
    print("done")


if __name__ == "__main__":
    main()
