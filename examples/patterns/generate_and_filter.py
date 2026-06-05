"""Pattern: generate-and-filter (Anthropic best practice).

Generate many candidates, then filter by a rubric / verification, dedupe, and
return only the highest-quality survivors. Fights agentic-laziness: generation
and judgment are separate, verified steps.

    flow run examples/patterns/generate_and_filter.py --args '{"topic":"growth ideas for a CLI tool","k":8}'
"""

IDEAS = {"type": "object", "required": ["ideas"], "properties": {"ideas": {"type": "array", "items": {"type": "string"}}}}
KEEP = {"type": "object", "required": ["keep"], "properties": {"keep": {"type": "boolean"}, "reason": {"type": "string"}}}


def run(wf, args):
    topic = args["topic"]
    k = (args or {}).get("k", 8)

    wf.phase("generate")
    gen = wf.agent(f"Generate {k} distinct ideas for: {topic}. JSON {{ideas:[...]}}.",
                   label="generate", schema=IDEAS, tier="quality")
    ideas = list(dict.fromkeys((gen or {}).get("ideas", [])))  # dedupe, preserve order

    wf.phase("filter")
    verdicts = wf.parallel([
        (lambda i=i: {"idea": i, "v": wf.agent(
            f"Is this idea genuinely high-quality and non-obvious? Default keep=false if generic. Idea: {i}",
            label="filter", schema=KEEP, tier="cheap", required=False)})
        for i in ideas
    ])
    survivors = [x["idea"] for x in verdicts if x and x.get("v") and x["v"].get("keep")]
    return {"considered": len(ideas), "survivors": survivors}
