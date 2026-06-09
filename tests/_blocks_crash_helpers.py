"""Subprocess helper for the block-charge resume test (plan §11.4, WS-C).

Two-iteration loop sharing one context block; hard-exits inside iteration 2
after the iteration's block charge + first sibling commit but BEFORE the second
sibling runs, exactly once (crash marker). The resumed lifetime must replay
both charges, and the rerun iteration's fresh sibling must not re-charge.
"""
from __future__ import annotations

import os
import sys

from flow.loop import LoopSpec
from flow.runtime import run_workflow

RUN_ID = "blocks-crash-001"
SCRIPT_ID = "blocks-crash-script"
BLOB = "shared context line\n" * 200  # 4000 chars -> 1000 estimated tokens


def build_run(crash_marker: str):
    def step(wf, ctx):
        n = ctx["iteration"]
        ref = wf.block(BLOB)
        a = wf.agent(f"work {n}a: {ref}", label=f"w{n}a", model="echo", max_tokens=40)
        if n == 2 and not os.path.exists(crash_marker):
            open(crash_marker, "w").close()
            os._exit(137)
        b = wf.agent(f"work {n}b: {ref}", label=f"w{n}b", model="echo", max_tokens=40)
        return [a, b]

    def run(wf, args):
        wf.phase("work")
        return wf.loop(spec=LoopSpec(goal="block-resume", max_iterations=2), step=step)
    return run


def main():
    run_workflow(run_fn=build_run(sys.argv[1]), run_id=RUN_ID, script_id=SCRIPT_ID,
                 slug="blocks", max_workers=1, budget={"max_tokens": 100000})


if __name__ == "__main__":
    main()
