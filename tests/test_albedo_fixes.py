"""Fixes from Albedo's flow audit (2026-06-05)."""
from flow import leaves, progress
from flow.backends.base import BackendResponse
from flow.ids import leaf_id
from flow.runtime import run_workflow
from flow import schema as schema_mod


# -- leaf identity includes schema (cache-collision fix) -------------------
def test_leaf_id_distinguishes_schema():
    base = dict(run_script="s", phase="p", label="l", prompt="x", model="m", backend="openai_http")
    a = leaf_id(**base, schema="")
    b = leaf_id(**base, schema="abc")
    assert a != b  # different output schema => different work => different id


def test_same_prompt_different_schema_not_cached(monkeypatch):
    S1 = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    S2 = {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}}
    calls = {"n": 0}

    def factory(req):
        def backend(prompt, *, timeout=None):
            calls["n"] += 1
            return BackendResponse('{"a":"1","b":"2"}', 5, 5)
        return backend

    monkeypatch.setattr(leaves, "build_backend", factory)

    def run(wf, args):
        wf.phase("p")
        r1 = wf.agent("same prompt", label="x", schema=S1, tier="quality")
        r2 = wf.agent("same prompt", label="x", schema=S2, tier="quality")
        return [r1, r2]

    run_workflow(run_fn=run, slug="schemacache")
    assert calls["n"] == 2  # both ran — NOT collapsed into one cache hit


# -- full JSON Schema validation (jsonschema) ------------------------------
def test_full_jsonschema_enum_and_nested():
    sch = {"type": "object", "required": ["sev", "meta"],
           "properties": {"sev": {"type": "string", "enum": ["low", "high"]},
                          "meta": {"type": "object", "required": ["n"],
                                   "properties": {"n": {"type": "integer"}}}}}
    ok, _, _ = schema_mod.parse('{"sev":"high","meta":{"n":3}}', sch)
    assert ok
    bad_enum, _, err = schema_mod.parse('{"sev":"medium","meta":{"n":3}}', sch)
    assert not bad_enum  # jsonschema catches the bad enum (subset check would not)
    bad_nested, _, _ = schema_mod.parse('{"sev":"low","meta":{"n":"x"}}', sch)
    assert not bad_nested  # nested type error caught


# -- phase scoping under concurrency ---------------------------------------
def test_phase_scoping_parallel():
    import time

    def slow(x):
        time.sleep(0.05)
        return x

    def run(wf, args):
        wf.phase("scan")
        # submit under "scan", then immediately move main-thread phase to "later"
        fut = wf.parallel([(lambda i=i: wf.local(slow, i, label=f"s{i}")) for i in range(4)])
        wf.phase("later")
        return fut

    rid = "phase-scope-1"
    run_workflow(run_fn=run, run_id=rid, script_id="ps", slug="ps")
    agg = progress._aggregate(rid)
    # all four leaves must be recorded under "scan", not "later"
    assert "scan" in agg["by_phase"] and len(agg["by_phase"]["scan"]) == 4
    assert "later" not in agg["by_phase"] or not agg["by_phase"].get("later")
