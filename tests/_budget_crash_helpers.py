"""Subprocess helper for the budget-replay-on-resume crash test (plan §3.4).

Four shell-backend leaves with real (estimated) token spend; hard-exits after
leaves 0–1 commit and journal, exactly once (crash marker).
"""
from __future__ import annotations

import os
import sys

from flow.runtime import run_workflow

RUN_ID = "budget-replay-001"
SCRIPT_ID = "budget-replay-script"
N = 4
MAX_TOKENS = 400
LEAF_MAX_TOKENS = 60
PROMPT_PAD = "x" * 220


def build_run(crash_marker):
    def run(wf, args):
        wf.phase("p")
        out = []
        for i in range(N):
            out.append(wf.agent(f"leaf {i} {PROMPT_PAD}", label=f"b{i}",
                                model="echo", max_tokens=LEAF_MAX_TOKENS))
            if i == 1 and not os.path.exists(crash_marker):
                open(crash_marker, "w").close()
                os._exit(137)
        return out
    return run


def main():
    crash_marker = sys.argv[1]
    run_workflow(run_fn=build_run(crash_marker), run_id=RUN_ID, script_id=SCRIPT_ID,
                 slug="budget", max_workers=1,
                 budget={"max_tokens": MAX_TOKENS, "max_calls": 60})


if __name__ == "__main__":
    main()
