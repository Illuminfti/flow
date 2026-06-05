"""Phase 3A: cross-run content-addressed cache (opt-in)."""
from flow import cache, config, leaves
from flow.backends.base import BackendResponse
from flow.runtime import run_workflow


def test_content_key_deterministic():
    a = cache.content_key("p", "sh", "m", "openai_http")
    b = cache.content_key("p", "sh", "m", "openai_http")
    c = cache.content_key("p", "DIFFERENT", "m", "openai_http")
    assert a == b and a != c


def test_cache_dir_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert str(tmp_path) in str(cache.cache_dir())


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache.store("k1", {"status": "completed", "value": "v"})
    assert cache.load("k1", None)["value"] == "v"
    assert cache.load("missing", None) is None


def _counting_backend(counter):
    def factory(req):
        def backend(prompt, *, timeout=None, **kw):
            counter["n"] += 1
            return BackendResponse("X", 5, 5, provider="test", model="big-model")
        return backend
    return factory


def test_disabled_by_default_recomputes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "d"))
    counter = {"n": 0}
    monkeypatch.setattr(leaves, "build_backend", _counting_backend(counter))

    def run(wf, args):
        wf.phase("p")
        return wf.agent("hi", label="q", tier="quality")

    run_workflow(run_fn=run, run_id="a", slug="x")
    run_workflow(run_fn=run, run_id="b", slug="x")  # different run, cache OFF
    assert counter["n"] == 2  # recomputed both times


def test_enabled_hits_across_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("FLOW_DATA_DIR", str(tmp_path / "d"))
    cfg = config.get()
    cfg["leaf"]["content_cache"] = {"enabled": True, "ttl_days": None}
    config.set_config(cfg)
    counter = {"n": 0}
    monkeypatch.setattr(leaves, "build_backend", _counting_backend(counter))

    def run(wf, args):
        wf.phase("p")
        return wf.agent("hi", label="q", tier="quality")

    run_workflow(run_fn=run, run_id="a", slug="x")
    run_workflow(run_fn=run, run_id="b", slug="x")  # different run, cache ON -> global hit
    assert counter["n"] == 1  # computed once, reused across runs
