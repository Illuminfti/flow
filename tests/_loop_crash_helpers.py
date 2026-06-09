"""Subprocess helper for the loop crash-resume test (plan WS-B / §5.2).

Four-iteration loop over local leaves; hard-exits inside iteration 3's step,
exactly once (crash marker), after iterations 1–2 commit and journal. The
exec log records every step() invocation so the test can prove completed
iterations never rerun.
"""
from __future__ import annotations

import os
import sys

from flow.loop import LoopSpec
from flow.runtime import run_workflow

RUN_ID = "loop-crash-001"
SCRIPT_ID = "loop-crash-script"
N = 4


def build_run(crash_marker: str, exec_log: str):
    def step(wf, ctx):
        n = ctx["iteration"]
        with open(exec_log, "a") as f:
            f.write(f"step{n}\n")
        if n == 3 and not os.path.exists(crash_marker):
            open(crash_marker, "w").close()
            os._exit(137)
        return wf.local(lambda i: i * 10, n, label=f"s{n}")

    def verify(wf, ctx):
        return wf.local(lambda c: {"verdict": "accept", "value": c},
                        ctx["candidate"], label=f"v{ctx['iteration']}")

    def run(wf, args):
        wf.phase("work")
        return wf.loop(spec=LoopSpec(goal="crash-resume", max_iterations=N),
                       step=step, verify=verify)
    return run


def main():
    run_workflow(run_fn=build_run(sys.argv[1], sys.argv[2]), run_id=RUN_ID,
                 script_id=SCRIPT_ID, slug="loop", max_workers=2)


if __name__ == "__main__":
    main()
