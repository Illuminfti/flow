"""Pattern: classify-and-act (Anthropic dynamic-workflows best practice).

A cheap classifier decides the task type, then routes to the right handler /
tier. Fights goal-drift: the route is decided once, explicitly, up front.

    flow run examples/patterns/classify_and_act.py --args '{"task":"refactor the auth module"}'
"""

CLASS = {"type": "object", "required": ["kind"],
         "properties": {"kind": {"type": "string", "enum": ["code", "research", "writing", "other"]}}}


def run(wf, args):
    task = args["task"]
    wf.phase("classify")
    c = wf.agent(f"Classify this task into one of code|research|writing|other. Task: {task}",
                 label="classify", schema=CLASS, tier="cheap")
    kind = (c or {}).get("kind", "other")

    wf.phase("act")
    # route to a tier/handler by class — cheap brains for cheap work, quality for hard
    tier = {"code": "quality", "research": "quality", "writing": "cheap"}.get(kind, "cheap")
    result = wf.agent(f"Handle this {kind} task end to end: {task}", label=f"act:{kind}", tier=tier)
    return {"kind": kind, "result": result}
