"""Pre-hooks — run after the session loads, BEFORE the LLM.

Use for input validation, safety gates, PII checks, and preloading context into
session state. Raise ``InputCheckError`` to halt the run before spending a token
(this is how eugenia's handoff-gate and renan's urgency-gate stop unwanted runs).

Agno injects only the params you declare: run_input, agent, session, run_context,
user_id, metadata, debug_mode.

Docs — Agno pre/post hooks: https://docs.agno.com/hooks/overview
"""

from __future__ import annotations

import os
import re
from typing import Any

from agno.exceptions import CheckTrigger, InputCheckError
from agno.run.agent import RunInput

# Cap on inbound text — a cost/DoS guard against absurd inputs, not a content rule.
# ⚠️ It also bounds MEDIA that a channel pre-processes to text before the agent:
# Omni injects a doc's extracted text / an image's vision description as the message
# (📄/🖼 …), which for a long PDF or report easily blows past a chat-sized cap. The
# old 8000 (~2k tokens) blocked real documents. Default is now generous but bounded
# and env-tunable — raise MAX_INPUT_CHARS for doc-heavy agents, lower it for
# untrusted chat-only ones. See docs/omni.md (media flows through as text).
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "50000"))

# Toy prompt-injection / abuse signal. Replace with your real guardrail.
_BLOCKED = re.compile(r"\b(ignore (all|previous) instructions|system prompt)\b", re.IGNORECASE)


def validate_input(run_input: RunInput) -> None:
    """Reject oversized or obviously adversarial input before the model sees it."""
    text = str(run_input.input_content or "")
    if len(text) > MAX_INPUT_CHARS:
        raise InputCheckError(
            f"Input too long (>{MAX_INPUT_CHARS} chars).",
            check_trigger=CheckTrigger.INPUT_NOT_ALLOWED,
        )
    if _BLOCKED.search(text):
        raise InputCheckError(
            "Input rejected by safety guardrail.",
            check_trigger=CheckTrigger.INPUT_NOT_ALLOWED,
        )


def preload_context(run_input: RunInput, run_context: Any = None) -> None:
    """Example: seed session_state before the run (e.g. look up a user dossier).

    Real agents do a DB/API lookup here keyed by user_id and stash it in
    session_state so tools and instructions can use it. This stub just marks that
    a turn happened.
    """
    if run_context is None:
        return
    state = run_context.session_state
    state["turns"] = state.get("turns", 0) + 1


def enrich_trace(
    run_input: RunInput,
    run_context: Any = None,
    agent: Any = None,
    user_id: str | None = None,
) -> None:
    """Stamp rich, per-request metadata onto the observability trace.

    Static resource metadata (service/version/env/model) is set once in
    observability.py (OTel Resource). This hook adds the *dynamic* bits so traces
    are filterable in LangWatch by user, conversation and any app-specific context.
    Agno already sets user/thread from run params; we (re)assert them and show how
    to attach custom fields (here: the turn count). Safe no-op when tracing is off.

    We also bind ``user_id`` / ``session_id`` into the logging context here, so
    every log line for the rest of this turn carries them (cleared in a post-hook).
    """
    from ..log import bind_request_context
    from ..observability import enrich_current_trace

    session_id = getattr(run_context, "session_id", None) if run_context else None
    turns = run_context.session_state.get("turns") if run_context else None

    bind_request_context(user_id=user_id, session_id=session_id)

    enrich_current_trace(
        user_id=user_id,
        session_id=session_id,
        # Real agents add tenant/channel/plan/etc. here (per-request context):
        metadata={"turn": turns},
    )


def tag_experiment(
    run_context: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Stamp the A/B arm onto the trace when this run is part of an experiment.

    The experiment endpoint (experiments/routes.py) passes the assignment as run
    ``metadata`` (``ab_experiment`` / ``ab_variant`` / ``ab_variant_version``).
    This hook reads it and tags the current span so LangWatch can slice metrics by
    variant. It's a no-op for ordinary runs that carry no such metadata — including
    direct ``/agents/{id}/runs`` calls — so it's safe on every agent.
    """
    meta = metadata or (getattr(run_context, "metadata", None) if run_context else None) or {}
    experiment = meta.get("ab_experiment")
    variant = meta.get("ab_variant")
    if not experiment or not variant:
        return
    from ..observability import tag_experiment as tag_experiment_on_trace

    tag_experiment_on_trace(experiment, variant, meta.get("ab_variant_version"))


# Order matters: preload_context bumps the turn counter before enrich_trace reads it.
PRE_HOOKS = [validate_input, preload_context, enrich_trace, tag_experiment]
