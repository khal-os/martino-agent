"""The template agent. Copy this module to create a new agent.

Checklist for a new agent (see docs/adding-an-agent.md):
  1. Copy this file to ``agents/<your_agent>.py`` and adjust id/tools/hooks.
  2. Create its prompt at ``prompts/<your_agent>/system.md``.
  3. Register the builder in ``agents/__init__.py`` (BUILDERS).
  4. Add at least one eval case in ``evals/cases.py`` and a test in ``tests/``.
"""

from __future__ import annotations

from agno.agent import Agent

from ..config import Settings
from ..context import build_context_message
from ..db import build_db
from ..hooks import POST_HOOKS, PRE_HOOKS, logging_tool_hook
from ..knowledge import build_knowledge
from ..models import build_fallback_config, build_model
from ..prompt_loader import load_prompt
from ..tools import EXAMPLE_TOOLS


def build_assistant(
    settings: Settings,
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
    prompt_variant: str = "system",
) -> Agent:
    """Build the assistant. The keyword overrides exist so the same builder can
    produce **A/B variants**: register it twice with a different ``agent_id`` +
    ``prompt_variant`` (and/or ``model_id`` via a partial) — see agents/__init__.py
    and experiments/registry.py. A variant is just a registered Agent with a stable
    id; nothing else about the wiring changes."""
    name = agent_name or settings.agent_name
    return Agent(
        id=agent_id or settings.agent_id,
        name=name,
        model=build_model(settings),
        # Model fallback: on error/rate-limit/context-overflow, retry the run on
        # FALLBACK_MODEL_ID (None-safe: feature off when unset). See models.py.
        fallback_config=build_fallback_config(settings),
        db=build_db(settings),
        knowledge=build_knowledge(settings),  # None-safe: agent runs without a KB
        # Let the model search the knowledge base on demand (agentic RAG).
        search_knowledge=settings.knowledge_enabled,
        tools=EXAMPLE_TOOLS,
        # --- Hooks ---
        tool_hooks=[logging_tool_hook],
        pre_hooks=PRE_HOOKS,
        post_hooks=POST_HOOKS,
        # --- Session / state / memory ---
        session_state={"cart": [], "turns": 0},  # default initial state
        add_session_state_to_context=True,  # let the model see current state
        add_history_to_context=True,  # multi-turn memory
        num_history_runs=10,
        # --- Instructions (STATIC → cached system prompt; file-managed) ---
        instructions=load_prompt("assistant", variant=prompt_variant, agent_name=name),
        markdown=True,
        # --- Prompt-caching hygiene (see context.py / docs/prompt-caching.md) ---
        # Do NOT let Agno inject a live timestamp into the cached system prompt —
        # it would bust the cache every turn. The date (and any per-request
        # context) is injected as a "message 0" instead.
        add_datetime_to_context=False,
        additional_input=[build_context_message(settings)],
        debug_mode=settings.debug,
    )
