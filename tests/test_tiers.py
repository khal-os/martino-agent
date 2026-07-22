"""Model tier map — resolution and @alias support (offline)."""

import pytest

from agent_app import config
from agent_app.models import MODEL_TIERS, build_model, model_for_tier


@pytest.fixture(autouse=True)
def fresh_settings():
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_every_provider_has_all_three_tiers():
    for provider, tiers in MODEL_TIERS.items():
        assert set(tiers) == {"fast", "medium", "high"}, provider
        assert all(tiers.values()), provider


def test_model_for_tier_resolves():
    assert model_for_tier("anthropic", "medium") == MODEL_TIERS["anthropic"]["medium"]
    assert model_for_tier("openai", "fast") == "gpt-5.6-luna"
    assert model_for_tier("gemini", "high") == MODEL_TIERS["google"]["high"]


def test_model_for_tier_unknowns_raise_helpfully():
    with pytest.raises(ValueError, match="fast, medium, high"):
        model_for_tier("anthropic", "turbo")
    with pytest.raises(ValueError, match="explicit model id"):
        model_for_tier("litellm", "fast")


def test_build_model_accepts_tier_alias(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_ID", "@fast")
    config.get_settings.cache_clear()
    model = build_model(config.get_settings())
    assert model.id == MODEL_TIERS["anthropic"]["fast"]


def test_build_model_tier_alias_for_fallback(monkeypatch):
    monkeypatch.setenv("FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("FALLBACK_MODEL_ID", "@medium")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    config.get_settings.cache_clear()
    from agent_app.models import build_fallback_config

    cfg = build_fallback_config(config.get_settings())
    assert cfg.on_error[0].id == "gpt-5.6-terra"
