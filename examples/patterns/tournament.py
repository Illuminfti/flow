"""Pattern: tournament / comparative judgment (Anthropic best practice).

N agents attempt the same task with different approaches; judges pick winners by
PAIRWISE comparison — comparative judgment is more reliable than absolute scoring.
Fights self-preferential bias: a separate judge compares, the author doesn't grade itself.

    flow run examples/patterns/tournament.py --args '{"task":"name this product","n":4}'
"""

PICK = {"type": "object", "required": ["winner"],
        "properties": {"winner": {"type": "string", "enum": ["A", "B"]}, "why": {"type": "string"}}}


def run(wf, args):
    task = args["task"]
    n = (args or {}).get("n", 4)
    approaches = ["bold/risky", "safe/conventional", "playful", "minimal"][:n]

    wf.phase("compete")
    entries = wf.parallel([
        (lambda a=a: {"approach": a, "answer": wf.agent(f"{task}. Approach: {a}. One concise answer.",
                                                        label=f"try:{a}", tier="quality")})
        for a in approaches
    ])
    entries = [e for e in entries if e and e.get("answer")]

    wf.phase("bracket")
    # single-elimination pairwise bracket
    while len(entries) > 1:
        pairs = [(entries[i], entries[i + 1]) for i in range(0, len(entries) - 1, 2)]
        carry = [entries[-1]] if len(entries) % 2 else []
        winners = wf.parallel([
            (lambda x=x, y=y: (x if (wf.agent(
                f"Task: {task}\nA: {x['answer']}\nB: {y['answer']}\nWhich is better? JSON {{winner:A|B}}.",
                label="judge", schema=PICK, tier="quality", required=False) or {}).get("winner") == "A" else y))
            for (x, y) in pairs
        ])
        entries = [w for w in winners if w] + carry
    return {"winner": entries[0] if entries else None}
