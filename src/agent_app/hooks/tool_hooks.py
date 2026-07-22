"""Tool hooks — wrap *every* tool call.

A tool hook is middleware around tool execution: you receive the callable and its
arguments, and you MUST call it and return its result (or raise to block). Agno
injects only the parameters your function declares. Supported names:
    agent, team, run_context, name/function_name, function/func/function_call, args/arguments

Attach via ``Agent(tool_hooks=[...])``. Multiple hooks nest outer→inner.

Docs — Agno tool hooks: https://docs.agno.com/tools/hooks
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..log import get_logger

logger = get_logger("agent_app.tools")


def logging_tool_hook(
    function_name: str, function_call: Callable[..., Any], arguments: dict[str, Any]
) -> Any:
    """Log every tool call with timing and outcome. Great default for observability.

    Emits structured key-value events (``tool.start`` / ``tool.ok`` / ``tool.error``)
    that carry the run's trace_id + bound user/session context automatically.
    """
    start = time.perf_counter()
    logger.info("tool.start", name=function_name, args=_preview(arguments))
    try:
        result = function_call(**arguments)
    except Exception:
        dur = (time.perf_counter() - start) * 1000
        logger.exception("tool.error", name=function_name, ms=round(dur))
        raise
    dur = (time.perf_counter() - start) * 1000
    logger.info("tool.ok", name=function_name, ms=round(dur))
    return result


def allowlist_tool_hook(allowed: set[str]) -> Callable[..., Any]:
    """Factory: block any tool whose name isn't in ``allowed`` (defense-in-depth)."""

    def hook(
        function_name: str, function_call: Callable[..., Any], arguments: dict[str, Any]
    ) -> Any:
        if function_name not in allowed:
            raise PermissionError(f"Tool '{function_name}' is not permitted in this context.")
        return function_call(**arguments)

    return hook


def _preview(obj: Any, limit: int = 200) -> str:
    text = repr(obj)
    return text if len(text) <= limit else text[:limit] + "…"
