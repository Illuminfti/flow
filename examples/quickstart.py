"""Minimal online flow workflow.

Requires a configured model route. Run:

    flow run examples/quickstart.py --args '{"target":"src/flow"}' --budget '{"max_usd":1,"max_calls":10}'
"""
from __future__ import annotations

from flow import run_workflow

FINDING = {
    "type": "object",
    "required": ["title", "severity"],
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string"},
    },
}


def run(wf, args):
    target = (args or {}).get("target", "src/")
    wf.phase("review")
    lenses = ["correctness", "security", "performance"]
    findings = wf.parallel([
        lambda lens=lens: wf.agent(
            f"Review {target} for one {lens} issue. Return JSON only.",
            label=f"review:{lens}",
            schema=FINDING,
            tier="quality",
            max_tokens=600,
        )
        for lens in lenses
    ])

    wf.phase("verify")
    return wf.parallel([
        lambda finding=finding: wf.agent(
            f"Try to refute this finding. If real, explain why: {finding}",
            label="verify",
            tier="cheap",
            required=False,
            max_tokens=500,
        )
        for finding in findings
        if finding
    ])


if __name__ == "__main__":
    report = run_workflow(
        run_fn=run,
        args={"target": "src/"},
        budget={"max_usd": 1, "max_calls": 10},
    )
    print(report["final"])
