"""Local-only self-test workflow — exercises phase/parallel/pipeline/local/
nested without a single model call. `flowleaf run flowleaf/templates/selftest.py`."""
import time


def _work(x):
    time.sleep(0.05)
    return x * x


def _double(prev, _orig, _idx):
    return prev * 2


def run(wf, args):
    n = (args or {}).get("n", 6)
    wf.phase("fan")
    squares = wf.parallel([(lambda i=i: wf.local(_work, i, label=f"sq{i}")) for i in range(n)])
    wf.phase("pipe")
    doubled = wf.pipeline(squares, _double)

    def child(wf, inp):
        return wf.parallel([(lambda v=v: wf.local(_work, v, label=f"c{v}")) for v in inp])

    wf.phase("nest")
    nested = wf.workflow(child, [1, 2, 3], label="sub")
    return {"squares": squares, "doubled": doubled, "nested": nested, "spend": wf.spend()}
