"""Agent registry — the single place new agents are plugged in.

One module per agent under ``agents/`` (community pattern from the official
agno ``agentos-docker-template``). To add an agent:

    1. create ``agents/<name>.py`` exposing ``build_<name>(settings) -> Agent``
    2. add it to ``BUILDERS`` below
    3. prompt at ``prompts/<name>/system.md``; eval case in ``evals/cases.py``

Everything downstream (AgentOS routes, evals, health) iterates this registry.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache, partial

from agno.agent import Agent

from ..config import Settings, get_settings
from .assistant import build_assistant

AgentBuilder = Callable[[Settings], Agent]

# id-ish key → builder. Order defines display order in AgentOS.
#
# ``assistant-concise`` is variant B of the ``assistant-tone`` A/B experiment
# (experiments/registry.py): the SAME builder with a different id + prompt. This
# is the Agno-blessed way to do A/B — distinct registered Agents sharing one db —
# and it means each arm is also reachable directly at /agents/{id}/runs.
BUILDERS: dict[str, AgentBuilder] = {
    "assistant": build_assistant,
    "assistant-concise": partial(
        build_assistant,
        agent_id="assistant-concise",
        agent_name="Assistant (concise)",
        prompt_variant="concise",
    ),
    # "researcher": build_researcher,
}


@lru_cache
def get_agents() -> tuple[Agent, ...]:
    """Build every registered agent exactly once per process (never in loops/requests)."""
    settings: Settings = get_settings()
    return tuple(builder(settings) for builder in BUILDERS.values())


def get_agent(agent_id: str) -> Agent:
    for agent in get_agents():
        if agent.id == agent_id:
            return agent
    raise KeyError(f"No agent with id '{agent_id}'. Registered: {[a.id for a in get_agents()]}")
