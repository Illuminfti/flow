"""audit-hard — finder pool → adversarial refutation → ranked fixes.

The real-model successor to the old stub's hardcoded template. Drive with:
  flow run flow/templates/audit_hard.py --args '{"target":"path or topic",
      "lenses":["correctness","security","operations","taste"]}'
      --budget '{"max_usd":3,"max_tokens":400000}'
"""
FINDING = {
    "type": "object",
    "required": ["title", "severity", "evidence"],
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "evidence": {"type": "string"},
        "fix": {"type": "string"},
    },
}
VERDICT = {
    "type": "object",
    "required": ["real", "reason"],
    "properties": {"real": {"type": "boolean"}, "reason": {"type": "string"}},
}


def run(wf, args):
    args = args or {}
    target = args["target"]
    lenses = args.get("lenses", ["correctness", "security", "operations", "taste"])

    wf.phase("find")
    found = wf.parallel([
        (lambda lens=lens: wf.agent(
            f"Audit {target} through the {lens} lens. Return the single most important finding "
            f"with concrete path:line evidence and a fix.",
            label=f"find:{lens}", schema=FINDING, tier="quality", required=False))
        for lens in lenses
    ])
    findings = [f for f in found if f]

    wf.phase("verify")
    verified = wf.parallel([
        (lambda f=f: {"finding": f, "verdict": wf.agent(
            f"Adversarially verify this finding. Default to real=false if uncertain. "
            f"Finding: {f}", label=f"verify:{f['title'][:24]}", schema=VERDICT,
            tier="cheap", required=False)})
        for f in findings
    ])
    confirmed = [v["finding"] for v in verified if v and v.get("verdict") and v["verdict"].get("real")]

    wf.phase("rank")
    ranking = wf.agent(
        f"Rank these confirmed findings by fix priority and give a one-line action each: {confirmed}",
        label="rank", tier="quality", required=False)
    return {"confirmed": confirmed, "ranking": ranking, "spend": wf.spend()}
