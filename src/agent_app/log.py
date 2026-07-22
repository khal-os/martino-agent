"""Structured logging — JSON in prod, pretty console in dev, trace-correlated.

One call to :func:`configure_logging` (from ``main.py``) sets up structlog and
routes the **stdlib** ``logging`` module through it, so every log line — ours
*and* the libraries' (agno, uvicorn, …) — comes out in the same shape.

Two things make this production-grade:

**1. Trace correlation.** :func:`_add_open_telemetry_context` stamps the active
OTel ``trace_id`` / ``span_id`` onto every record. Since LangWatch is OTel-based
(see ``observability.py``), a log line and its LangWatch trace share an id — you
can jump from one to the other. No-op when tracing is off.

**2. Request context.** :func:`bind_request_context` binds ``user_id`` /
``session_id`` (and anything else) into a contextvar, so *all* logs emitted
during that agent turn carry them without threading arguments everywhere. A
pre-hook binds it; a post-hook clears it.

Usage anywhere::

    from .log import get_logger
    log = get_logger(__name__)
    log.info("tool.ok", name="search_web", ms=42)   # key-value, not f-strings

Docs — structlog stdlib integration: https://www.structlog.org/en/stable/standard-library.html
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# structlog's typed aliases keep get_logger()/processors honest under mypy strict.
from structlog.typing import EventDict, Processor, WrappedLogger

_CONFIGURED = False


def _add_open_telemetry_context(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    """Attach ``trace_id`` / ``span_id`` from the active OTel span, when present.

    Mirrors ``observability.current_trace_id()`` so logs and LangWatch traces are
    joinable on the same 32-hex id. Import is guarded: without the observability
    extras there's no ``opentelemetry`` and this is a no-op.
    """
    try:
        from opentelemetry import trace
    except ImportError:
        return event_dict

    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(*, json_logs: bool = True, level: str = "INFO") -> None:
    """Configure structlog + stdlib logging. Idempotent (safe under uvicorn reload).

    ``json_logs`` → one JSON object per line (ship to Loki/CloudWatch/…);
    ``False`` → colorized, human-readable console output for local dev.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Processors shared by structlog-native and stdlib-originated ("foreign") logs,
    # so a `logging.getLogger(...)` call and a `get_logger(...)` call look identical.
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # per-request bound context
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _add_open_telemetry_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,  # applied to logs coming from stdlib loggers
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; drop them so lines aren't emitted twice.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Prefer this over ``logging.getLogger``."""
    return structlog.stdlib.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """Bind per-request fields (user_id, session_id, …) onto every subsequent log.

    ``None`` values are dropped so we don't emit ``user_id=null`` noise. Call from
    a pre-hook; pair with :func:`clear_request_context` in a post-hook.
    """
    clean = {k: v for k, v in kwargs.items() if v is not None}
    if clean:
        structlog.contextvars.bind_contextvars(**clean)


def clear_request_context() -> None:
    """Clear request-scoped context so it can't leak into the next turn."""
    structlog.contextvars.clear_contextvars()
