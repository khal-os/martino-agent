"""Volatile context injection — the prompt-caching technique.

## Why this exists
Anthropic prompt caching caches a **prefix**. The system prompt is that prefix.
If anything volatile lives inside it (current time, per-user data, memories), the
prefix changes → the cache is busted → you pay full price + lose TTFT every turn.

## The rule
- **Static** (persona, rules, tools, format) → the **system prompt** (cached).
- **Volatile** (today's date, user dossier, session context) → injected as a
  **"message 0"** that sits *after* the cached system prompt, in the conversation.

We do this with the model-agnostic Agno hooks:
- `add_datetime_to_context=False` — stop Agno injecting a live timestamp into the
  (cached) system prompt.
- `additional_input=[build_context_message(...)]` — Agno adds these messages to
  the run *after* the system message. This is our cache-safe "message 0".

## Two granularities
1. **Process-stable volatile** (the date at day granularity): built once at agent
   construction from `BUILD_DATE`, so the prefix + message-0 stay stable for the
   whole process. This is eugenia's approach. Good default.
2. **Per-request volatile** (this user's dossier): pass a fresh message-0 per run
   with ``agent.run(input=[build_context_message(user_ctx), user_message])`` — see
   ``run_with_context`` below. Keeps the system prompt cache shared across *all*
   users while still giving the model per-request context.

Docs — Anthropic prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
"""

from __future__ import annotations

from typing import Any

from agno.models.message import Message

from .config import Settings


def build_context_block(settings: Settings, user_context: dict[str, Any] | None = None) -> str:
    """Render the volatile context as plain text."""
    lines = [
        "<runtime_context>",
        f"Today is {settings.build_date}.",
    ]
    if user_context:
        lines.append("User context:")
        for key, value in user_context.items():
            lines.append(f"- {key}: {value}")
    lines.append("</runtime_context>")
    return "\n".join(lines)


def build_context_message(
    settings: Settings, user_context: dict[str, Any] | None = None
) -> Message:
    """The 'message 0' — volatile context as a user-role message (NOT the system prompt).

    ``add_to_agent_memory=False``: this block is re-injected fresh every run, so it
    must NOT also accumulate inside conversation history (that would replay stale
    copies and burn tokens).

    ⚠️ Date freshness: when used as the agent's construction-time ``additional_input``
    (see agents/assistant.py), ``build_date`` is frozen for the process lifetime
    (``get_settings()`` is cached). That's the eugenia trade-off — the date is
    day-granular and advances on **redeploy** (set ``BUILD_DATE`` in CI), not at
    midnight. For truly per-request context (a user dossier, the live date), use
    ``run_with_context`` on an agent built WITHOUT ``additional_input``.
    """
    return Message(
        role="user",
        content=build_context_block(settings, user_context),
        add_to_agent_memory=False,
    )


def run_with_context(
    agent: Any, user_message: str, user_context: dict[str, Any], **kwargs: Any
) -> Any:
    """Run the agent injecting fresh per-request volatile context as message 0.

    The system prompt (cached) stays byte-stable across all users; only this
    prepended message carries the per-request data (and it's evaluated *now*, so
    the date is truly current — unlike the construction-time path).

    ⚠️ Use this only on an agent built WITHOUT ``additional_input`` — Agno appends
    ``additional_input`` to *every* run, so mixing both double-injects the context
    block. This helper is the alternative to the construction-time approach used by
    the default ``assistant`` agent, not an addition to it.
    """
    from .config import get_settings

    ctx = build_context_message(get_settings(), user_context)
    return agent.run(input=[ctx, Message(role="user", content=user_message)], **kwargs)
