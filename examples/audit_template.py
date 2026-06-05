"""Finder pool -> adversarial verify -> rank. The canonical review workflow.

    flowleaf run examples/audit_template.py \
      --args '{"target":"./src","lenses":["correctness","security","performance"]}' \
      --budget '{"max_usd":2}'
"""

FINDING = {
    "type": "object", "required": ["title", "severity", "evidence"],
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "evidence": {"type": "string"},
        "fix": {"type": "string"},
    },
}
VERDICT = {"type": "object", "required": ["real", "reason"],
           "properties": {"real": {"type": "boolean"}, "reason": {"type": "string"}}}


def run(wf, args):
    target = args["target"]
    lenses = args.get("lenses", ["correctness", "security", "operations"])

    wf.phase("find")
    found = wf.parallel([
        (lambda l=l: wf.agent(f"Audit {target} through the {l} lens. Worst finding only, with evidence.",
                              label=f"find:{l}", schema=FINDING, tier="quality", required=False))
        for l in lenses
    ])
    findings = [f for f in found if f]

    wf.phase("verify")
    verified = wf.parallel([
        (lambda f=f: {"finding": f, "verdict": wf.agent(
            f"Adversarially verify. Default real=false if unsure. Finding: {f}",
            label="verify", schema=VERDICT, tier="cheap", required=False)})
        for f in findings
    ])
    confirmed = [v["finding"] for v in verified if v and v.get("verdict") and v["verdict"].get("real")]

    wf.phase("rank")
    ranking = wf.agent(f"Rank by fix priority, one action each: {confirmed}",
                       label="rank", tier="quality", required=False)
    return {"confirmed": confirmed, "ranking": ranking, "spend": wf.spend()}
