"""Fan out N questions concurrently, collect answers.

    flow run examples/simple_parallel.py --args '{"topics":["sui","solana","ethereum"]}'
"""


def run(wf, args):
    topics = (args or {}).get("topics", ["python", "rust", "go"])
    wf.phase("ask")
    answers = wf.parallel([
        (lambda t=t: wf.agent(f"In one sentence, what is {t} best at?", label=f"q:{t}", tier="cheap"))
        for t in topics
    ])
    return dict(zip(topics, answers))
