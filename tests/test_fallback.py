"""Model fallback — wiring tests (offline) + env-at-instantiation regression."""

import pytest

from agent_app import config


@pytest.fixture(autouse=True)
def fresh_settings():
    """Each test reads env fresh; leave a clean cache for the other test files."""
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_settings_read_env_at_instantiation(monkeypatch):
    """Regression: dataclass defaults must NOT freeze env values at import time."""
    monkeypatch.setenv("MODEL_ID", "some-brand-new-model")
    config.get_settings.cache_clear()
    assert config.get_settings().model_id == "some-brand-new-model"


def test_fallback_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FALLBACK_MODEL_ID", raising=False)
    config.get_settings.cache_clear()
    from agent_app.models import build_fallback_config

    assert build_fallback_config(config.get_settings()) is None


def test_fallback_config_wires_cross_provider(monkeypatch):
    monkeypatch.setenv("FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("FALLBACK_MODEL_ID", "gpt-5.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    config.get_settings.cache_clear()

    from agno.models.openai import OpenAIChat

    from agent_app.models import build_fallback_config

    cfg = build_fallback_config(config.get_settings())
    assert cfg is not None
    # The same fallback model covers all three failure triggers.
    for trigger in (cfg.on_error, cfg.on_rate_limit, cfg.on_context_overflow):
        assert len(trigger) == 1
        assert isinstance(trigger[0], OpenAIChat)
        assert trigger[0].id == "gpt-5.5"


def test_agent_carries_fallback_config(monkeypatch):
    monkeypatch.setenv("FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("FALLBACK_MODEL_ID", "gpt-5.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    config.get_settings.cache_clear()

    from agent_app.agents.assistant import build_assistant

    agent = build_assistant(config.get_settings())
    assert agent.fallback_config is not None
    assert agent.fallback_config.on_error[0].id == "gpt-5.5"


def test_fallback_end_to_end_offline(monkeypatch):
    """Full agno fallback path, no network: primary 503s → fallback's answer is served.

    Agno's rules (models/fallback.py): fallback fires on 5xx/network/429/529/
    context-overflow. It deliberately does NOT fire on other 4xx (caller bugs).
    """
    monkeypatch.setenv("FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("FALLBACK_MODEL_ID", "gpt-5.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    config.get_settings.cache_clear()
    settings = config.get_settings()

    from agno.agent import Agent
    from agno.exceptions import ModelProviderError
    from agno.models.response import ModelResponse

    from agent_app.models import build_fallback_config, build_model

    primary = build_model(settings)

    def outage(*args, **kwargs):
        raise ModelProviderError(
            message="synthetic outage",
            status_code=503,
            model_name=primary.name,
            model_id=primary.id,
        )

    monkeypatch.setattr(primary, "response", outage, raising=False)

    cfg = build_fallback_config(settings)
    fallback_model = cfg.on_error[0]
    monkeypatch.setattr(
        fallback_model,
        "response",
        lambda *a, **k: ModelResponse(content="FALLBACK-OK", role="assistant"),
        raising=False,
    )

    agent = Agent(model=primary, fallback_config=cfg, markdown=False)
    out = agent.run(input="hi")
    # The content proves the fallback served — the primary can only raise.
    # (Quirk: out.model still reports the primary's id; don't assert on it.)
    assert "FALLBACK-OK" in str(out.content)
