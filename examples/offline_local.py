"""Offline example — runs with NO API keys, NO network. Good for CI / kicking tyres.

    flow run examples/offline_local.py --args '{"n":6}'
    flow trace <run_id> --json

Uses only `wf.local` (deterministic Python leaves) across parallel + pipeline +
a nested workflow, so it exercises the engine end-to-end without any model.
"""


def _square(x):
    return x * x


def _double(prev, _orig, _idx):
    return prev * 2


def run(wf, args):
    n = (args or {}).get("n", 6)
    wf.phase("fan")
    squares = wf.parallel([(lambda i=i: wf.local(_square, i, label=f"sq{i}")) for i in range(n)])
    wf.phase("pipe")
    doubled = wf.pipeline(squares, _double)

    def child(wf, inp):
        return wf.parallel([(lambda v=v: wf.local(_square, v, label=f"c{v}")) for v in inp])

    wf.phase("nest")
    nested = wf.workflow(child, [1, 2, 3], label="sub")
    return {"squares": squares, "doubled": doubled, "nested": nested, "spend": wf.spend()}
