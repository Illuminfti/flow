"""Pattern: loop-until-done (Anthropic best practice).

For tasks with an unknown amount of work, keep spawning agents until a stop
condition is met (dry rounds) — not a fixed iteration count. Bounded by budget
so it always terminates. Fights agentic-laziness on open-ended discovery.

    flow run examples/patterns/loop_until_done.py --args '{"target":"./src","max_rounds":5}' --budget '{"max_usd":3}'
"""

FOUND = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {"type": "string"}}}}


def run(wf, args):
    target = args["target"]
    max_rounds = (args or {}).get("max_rounds", 5)
    seen: set = set()
    dry = 0
    rnd = 0
    while dry < 2 and rnd < max_rounds and wf.has_headroom(min_tokens=2000):
        rnd += 1
        wf.phase(f"round-{rnd}")
        res = wf.agent(f"Find issues in {target} NOT already in this list: {sorted(seen)}. JSON {{items:[...]}}.",
                       label=f"scan-{rnd}", schema=FOUND, tier="quality", required=False)
        fresh = [i for i in (res or {}).get("items", []) if i not in seen]
        if not fresh:
            dry += 1
            continue
        dry = 0
        seen.update(fresh)
    return {"rounds": rnd, "found": sorted(seen)}
