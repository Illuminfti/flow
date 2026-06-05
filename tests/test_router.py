import pytest

from flowleaf import router


def test_denylist_blocks_light_models():
    for bad in ["gpt-4o-mini", "gemini-2.0-flash", "claude-haiku-4-5", "x-nano", "foo-lite"]:
        with pytest.raises(router.RouterError):
            router.choose(model=bad, provider="test")


def test_quality_tier_picks_configured_model():
    r = router.choose(tier="quality")
    assert r.model == "big-model" and r.backend == "openai_http"


def test_cheap_tier_min_cost():
    r = router.choose(tier="cheap", est_in_tokens=1000, est_out_tokens=1000)
    assert r.model == "cheap-model"  # cheaper than big


def test_capability_filter():
    # 'cheap' tier: cheapo lacks vision, big has it -> needs=vision must pick big
    r = router.choose(tier="cheap", needs={"vision"})
    assert r.model == "big-model"


def test_unsatisfiable_caps_raises():
    with pytest.raises(router.RouterError):
        router.choose(tier="quality", needs={"nonexistent_cap"})


def test_explicit_pin():
    r = router.choose(model="deepseek-x", provider="test")
    assert r.model == "deepseek-x" and r.provider == "test"


def test_allow_light_models_opt_out(monkeypatch):
    from flowleaf import config
    cfg = config.get()
    cfg["leaf"]["allow_light_models"] = True
    config.set_config(cfg)
    r = router.choose(model="gpt-4o-mini", provider="test")
    assert r.model == "gpt-4o-mini"
