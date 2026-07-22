"""Post-hooks — run AFTER the model responds, before returning to the user.

Use for output validation, compliance/formatting guardrails, and leak prevention.
Mutate ``run_output.content`` in place to transform, or raise ``OutputCheckError``
to reject. This is where eugenia strips provider errors / API paths from customer
text and renan de-dups pre-tool narration.

Agno injects: run_output, agent, session, run_context, user_id, metadata, debug_mode.

Docs — Agno pre/post hooks: https://docs.agno.com/hooks/overview
"""

from __future__ import annotations

import re

from agno.exceptions import CheckTrigger, OutputCheckError
from agno.run.agent import RunOutput

MAX_OUTPUT_CHARS = 6000

# Never let internal error/impl details leak to end users.
_LEAKS = re.compile(
    r"(Traceback|/api/v\d|Bearer\s+\w|sk-[A-Za-z0-9]{6,}|psycopg|litellm)", re.IGNORECASE
)


def sanitize_output(run_output: RunOutput) -> None:
    """Redact internal leakage from the model's final text."""
    content = run_output.content
    if not isinstance(content, str):
        return
    if _LEAKS.search(content):
        run_output.content = _LEAKS.sub("[redacted]", content)


def enforce_length(run_output: RunOutput) -> None:
    """Reject absurdly long outputs (e.g. for a chat channel with limits)."""
    content = run_output.content
    if isinstance(content, str) and len(content) > MAX_OUTPUT_CHARS:
        raise OutputCheckError(
            f"Output too long (>{MAX_OUTPUT_CHARS} chars).",
            check_trigger=CheckTrigger.OUTPUT_NOT_ALLOWED,
        )


def clear_log_context(run_output: RunOutput) -> None:
    """Drop the per-request logging context bound in the pre-hook.

    Contextvars are per-task, but clearing at the end of the turn keeps bound
    fields (user_id/session_id) from bleeding into an unrelated run that reuses
    the context (e.g. under a worker thread pool). Runs last, always.
    """
    from ..log import clear_request_context

    clear_request_context()


# clear_log_context runs last so user/session stay bound through the other hooks.
POST_HOOKS = [sanitize_output, enforce_length, clear_log_context]
