"""NL -> workflow script, authored by a model via the configured backend.

The model writes a Python script using only the ``wf.*`` API. Output is
validated before it runs: AST parse, stdlib-only import allowlist, and a
``local``-only dry run that verifies the DAG shape with zero model calls.
"""
from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import router as _router
from .backends import build_backend
from .leaves import LeafRequest
from .paths import data_root

ALLOWED_IMPORTS = {
    "json", "re", "math", "statistics", "itertools", "functools",
    "collections", "datetime", "textwrap", "random", "string",
}

_API_CONTRACT = '''\
You write Python workflow scripts for the flow engine.

A script defines exactly one function:

    def run(wf, args):
        ...
        return <final_result>

`wf` is the runtime. `args` is caller-provided input (may be None). Use ONLY
these wf methods — do not import the engine, import nothing except Python stdlib:

  wf.phase(name)
  wf.agent(prompt, *, label=None, schema=None, tier="quality"|"cheap"|"free"|"local",
           required=True) -> result          # one bounded model leaf
  wf.parallel([thunk, ...]) -> [results]      # thunks run CONCURRENTLY
  wf.pipeline(items, stage1, stage2, ...) -> [results]
  wf.workflow(child_fn, inputs, label=...) -> result   # nested
  wf.local(fn, *args) -> result               # deterministic no-model leaf
  wf.log(msg); wf.spend(); wf.remaining(); wf.has_headroom()

Concurrency: build a LIST of zero-arg lambdas, pass to wf.parallel. Bind loop
vars with defaults: `lambda d=d: wf.agent(...)`. schema = a JSON Schema dict ->
structured output.

Output ONLY the Python code in a single ```python fenced block. No prose.
'''

_FEWSHOT = '''\
Example — "review files for bugs across 3 lenses, verify each":

```python
FINDING = {"type": "object", "required": ["title", "severity"],
           "properties": {"title": {"type": "string"}, "severity": {"type": "string"}}}

def run(wf, args):
    files = args["files"]
    wf.phase("review")
    found = wf.parallel([
        (lambda lens=lens: wf.agent(f"Review {files} for {lens} bugs. Worst one only.",
                                    label=f"review:{lens}", schema=FINDING))
        for lens in ["correctness", "security", "performance"]])
    findings = [f for f in found if f]
    wf.phase("verify")
    verdicts = wf.parallel([
        (lambda f=f: wf.agent(f"Refute if false: {f}", label="verify", tier="cheap", required=False))
        for f in findings])
    return {"findings": findings, "verdicts": verdicts}
```
'''

_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)


class AuthoringError(RuntimeError):
    pass


def _extract_code(text: str) -> str:
    m = _FENCE.search(text or "")
    code = m.group(1).strip() if m else (text or "").strip()
    if not code:
        raise AuthoringError("model returned no code")
    return code


_BANNED_CALLS = {"__import__", "eval", "exec", "compile", "open", "globals", "locals", "vars"}


def validate(script_text: str) -> None:
    """Guardrail against a misbehaving *model*, NOT a security sandbox against an
    adversary — authored scripts run with full process privileges. For untrusted
    input, run with executor_kind="process" + OS-level isolation."""
    try:
        tree = ast.parse(script_text)
    except SyntaxError as exc:
        raise AuthoringError(f"syntax error: {exc}")

    # Only declarative statements at module scope — no top-level side effects.
    allowed_top = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import,
                   ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.Pass)
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # module docstring / bare literal
        if not isinstance(stmt, allowed_top):
            raise AuthoringError(f"disallowed module-level statement: {type(stmt).__name__} "
                                 "(only defs, imports, and constant assignments allowed)")

    has_run = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                    else [(node.module or "").split(".")[0]])
            for m in mods:
                if m and m not in ALLOWED_IMPORTS:
                    raise AuthoringError(f"disallowed import: {m} (stdlib allowlist only)")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BANNED_CALLS:
            raise AuthoringError(f"disallowed builtin call: {node.func.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise AuthoringError(f"disallowed dunder access: {node.attr}")
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True
    if not has_run:
        raise AuthoringError("script must define run(wf, args)")


def dry_run(script_text: str, args=None, estimate_cost: bool = False) -> dict:
    """Load + execute with every leaf forced to the local backend (no model
    calls) to verify the DAG shape before a real run. With ``estimate_cost``,
    also forecast spend by routing each agent leaf (no model call) and summing
    ``route.est_usd`` — the only place real prompt/schema/tier are visible."""
    from .budget import Budget
    from .runtime import Workflow, _load_script
    from .scheduler import Engine

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script_text)
        path = f.name
    mod = _load_script(path)
    rd = Path(tempfile.mkdtemp(prefix="flow-dry-"))
    engine = Engine(run_dir=rd, budget=Budget(max_calls=10000))
    cost = {"total_usd": 0.0, "total_input_tokens": 0, "total_output_tokens": 0, "by_label": []}

    class DryWorkflow(Workflow):
        def agent(self, prompt, **kw):
            if estimate_cost:
                try:
                    needs = set(kw.get("needs") or [])
                    if kw.get("schema") is not None:
                        needs.add("structured")
                    if kw.get("tools"):
                        needs.add("tool_call")
                    route = _router.choose(tier=kw.get("tier"), model=kw.get("model"),
                                           provider=kw.get("provider"), needs=needs)
                    ei = max(1, len(prompt) // 4)
                    eo = kw.get("max_tokens") or 400
                    mult = 2 if (kw.get("schema") is not None and route.backend != "local") else 1
                    usd = route.est_usd(ei, eo) * mult
                    cost["total_usd"] += usd
                    cost["total_input_tokens"] += ei * mult
                    cost["total_output_tokens"] += eo * mult
                    cost["by_label"].append({"label": kw.get("label"), "model": route.model,
                                             "provider": route.provider, "est_usd": round(usd, 6)})
                except Exception:
                    pass
            return self.local(lambda: f"<dry:{kw.get('label', 'agent')}>", label=kw.get("label"))

    wf = DryWorkflow(engine, script_id="dry")
    try:
        mod.run(wf, args)
    finally:
        engine.shutdown()
    out = {"ok": True, "leaf_count": engine.report()["leaf_count"]}
    if estimate_cost:
        cost["total_usd"] = round(cost["total_usd"], 6)
        out["cost"] = cost
    return out


def _one_shot(prompt: str, *, tier: Optional[str], timeout: float) -> str:
    route = _router.choose(tier=tier)
    req = LeafRequest(leaf_id="author", label="author", phase="author", prompt=prompt,
                      route=route, timeout=timeout)
    backend = build_backend(req)
    return backend(prompt, timeout=timeout).text


def author(nl_task: str, *, tier: Optional[str] = None, timeout: int = 180,
           chat_fn: Optional[Callable[[str], str]] = None) -> str:
    """NL -> validated script. Uses the configured backend (or an injected
    ``chat_fn`` for hosts that want to supply their own model call)."""
    prompt = f"{_API_CONTRACT}\n{_FEWSHOT}\nNow write run(wf, args) for this task:\n{nl_task}"
    out = chat_fn(prompt) if chat_fn else _one_shot(prompt, tier=tier, timeout=timeout)
    code = _extract_code(out)
    validate(code)
    return code


def author_and_save(nl_task: str, out_path: Optional[str] = None, **kw) -> str:
    code = author(nl_task, **kw)
    if out_path is None:
        from .ids import stable_hash
        d = data_root() / "authored"
        d.mkdir(parents=True, exist_ok=True)
        out_path = str(d / f"wf-{stable_hash(nl_task, 8)}.py")
    Path(out_path).write_text(code, encoding="utf-8")
    return out_path
