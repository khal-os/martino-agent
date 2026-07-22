"""Smoke tests — no network, no model calls.

They assert the app *wires up* correctly (registry, factory, hooks, health
route). Behavior against the real model is covered by `make eval` (evals/).
"""

from __future__ import annotations


def test_settings_load():
    from agent_app.config import get_settings

    s = get_settings()
    assert s.agent_id
    assert s.port > 0


def test_registry_builds_all_agents():
    from agent_app.agents import BUILDERS, get_agent, get_agents

    agents = get_agents()
    assert len(agents) == len(BUILDERS) >= 1
    first = agents[0]
    # Hooks are attached.
    assert first.pre_hooks and first.post_hooks and first.tool_hooks
    # Tools are present; instructions came from the prompt file.
    assert first.tools
    assert (first.instructions and "assistant" in str(first.instructions).lower()) or first.name
    # Registry lookup by id works.
    assert get_agent(first.id) is first


def test_prompt_cache_hygiene():
    """The caching contract: static prefix + volatile message-0 (docs/prompt-caching.md)."""
    from agent_app.agents import get_agents

    agent = get_agents()[0]
    assert agent.add_datetime_to_context is False  # no live clock in cached prefix
    assert agent.additional_input  # volatile context as message-0
    model = agent.model
    if hasattr(model, "cache_system_prompt"):  # anthropic path
        assert model.cache_system_prompt is True


def test_app_has_health_route():
    from agent_app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in paths
