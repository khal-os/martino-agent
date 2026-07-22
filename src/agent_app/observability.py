"""LangWatch observability + rich trace metadata.

LangWatch is an OpenTelemetry-based LLM observability platform (self-hostable —
see docker-compose.langwatch.yml). We wire it at two levels:

**1. Resource / static metadata (every span in the process)** — set via
``langwatch.setup(base_attributes=...)``, which builds the OTel ``Resource``.
This is what turns ``service.name: unknown_service`` into your real service, and
stamps version + environment on every trace.

**2. Per-request metadata (this run)** — ``enrich_current_trace()`` attaches
user_id, thread_id (session), labels and any custom fields to the auto-created
trace. Called from a pre-hook so it runs on every agent turn.

Auto-instrumentation: the Agno OpenInference instrumentor traces every run/LLM/
tool call. Custom spans use ``langwatch.span(...)`` (see tools/example_tools.py).

Enable with ``LANGWATCH_ENABLED=1`` + ``LANGWATCH_ENDPOINT`` + ``LANGWATCH_API_KEY``.
No-op (and never crashes the app) when disabled or when deps aren't installed.

Docs — LangWatch Python SDK: https://langwatch.ai/docs/integration/python/guide
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from .config import Settings

logger = logging.getLogger("agent_app.observability")

_INITIALIZED = False


def _base_attributes(settings: Settings) -> dict[str, Any]:
    """OTel Resource attributes — stamped on every span (static, per-process)."""
    attrs = {
        # Standard OTel semantic-convention resource keys → nice grouping in any backend.
        "service.name": settings.service_name,
        "service.version": settings.agent_version,  # the agent version (single source)
        "deployment.environment": settings.environment,
        # Handy app-level context (shows up as trace attributes).
        "model.provider": settings.model_provider,
        "model.id": settings.model_id,
    }
    if settings.git_sha != "unknown":
        attrs["vcs.revision"] = settings.git_sha  # build/deploy provenance
    return attrs


def setup_observability(settings: Settings) -> None:
    global _INITIALIZED
    if _INITIALIZED or not settings.langwatch_enabled:
        return
    try:
        import langwatch

        instrumentors = []
        try:
            from openinference.instrumentation.agno import AgnoInstrumentor

            instrumentors.append(AgnoInstrumentor())
        except ImportError:
            logger.warning(
                "openinference-instrumentation-agno not installed; "
                "install extras: uv pip install -e '.[observability]'"
            )

        langwatch.setup(
            api_key=settings.langwatch_api_key,
            endpoint_url=settings.langwatch_endpoint,
            base_attributes=_base_attributes(settings),  # ← fixes service.name + rich resource
            instrumentors=instrumentors,
        )
        _INITIALIZED = True
        logger.info(
            "LangWatch enabled → %s (service=%s env=%s)",
            settings.langwatch_endpoint,
            settings.service_name,
            settings.environment,
        )
    except ImportError:
        logger.warning("langwatch not installed; skipping. Install '.[observability]'.")
    except Exception as exc:  # noqa: BLE001 — never let telemetry break the app
        logger.warning("LangWatch setup failed (%s); continuing without it.", exc)


# LangWatch reserved span-attribute keys (from langwatch/attributes.py). Setting
# these on the active span is the reliable way to enrich an auto-instrumented
# trace — get_current_trace() is None under the OpenInference instrumentor, so the
# native trace.update() path doesn't apply here.
_LW_USER_ID = "langwatch.user.id"
_LW_THREAD_ID = "langwatch.thread.id"
_LW_CUSTOMER_ID = "langwatch.customer.id"


def enrich_current_trace(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    customer_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Attach per-request metadata to the active (auto-instrumented) span.

    - ``user_id`` / ``session_id`` / ``customer_id`` use LangWatch reserved keys so
      the UI can group & filter by user, conversation (thread) and tenant.
    - ``metadata`` entries become ``app.<key>`` span attributes — backend-agnostic,
      always queryable (this is how you add tenant, channel, request-source, etc.).

    Static process-wide metadata (service/version/env/model) is set once on the
    OTel Resource in ``setup_observability`` — don't duplicate it per request.
    Safe no-op when tracing is off or no span is recording.
    """
    with contextlib.suppress(Exception):  # telemetry must never break a run
        from opentelemetry import trace as _otel

        span = _otel.get_current_span()
        if span is None or not span.is_recording():
            return
        if user_id:
            span.set_attribute(_LW_USER_ID, user_id)
        if session_id:
            span.set_attribute(_LW_THREAD_ID, session_id)
        if customer_id:
            span.set_attribute(_LW_CUSTOMER_ID, customer_id)
        for key, value in (metadata or {}).items():
            if value is not None:
                span.set_attribute(f"app.{key}", value)


def tag_experiment(experiment: str, variant: str, version: str | None = None) -> None:
    """Stamp the active trace with the A/B arm that served this run.

    Sets ``ab.experiment`` / ``ab.variant`` / ``ab.variant_version`` on the current
    (auto-instrumented) span. Those become group-by/filter dimensions in LangWatch,
    so you can slice quality, latency, tokens and cost **per variant** — which is
    the whole point: LangWatch is the measurement plane for the experiment.

    Must be called *during* the run (from a pre-hook) so the run's span is current
    — see hooks/pre_hooks.py:tag_experiment. Safe no-op when tracing is off.
    """
    with contextlib.suppress(Exception):  # telemetry must never break a run
        from opentelemetry import trace as _otel

        span = _otel.get_current_span()
        if span is None or not span.is_recording():
            return
        span.set_attribute("ab.experiment", experiment)
        span.set_attribute("ab.variant", variant)
        if version:
            span.set_attribute("ab.variant_version", version)


def track_event(
    trace_id: str,
    event_type: str,
    *,
    metrics: dict[str, float] | None = None,
    details: dict[str, str] | None = None,
) -> bool:
    """Record a custom user event against a trace (LangWatch ``POST /api/track_event``).

    This is how product signals — 👍/👎 feedback, ratings, "converted", "escalated"
    — get attached to the trace that produced them, so you can slice quality by
    real outcomes. ``trace_id`` comes from the run (see ``current_trace_id()``).

    Returns True on success; never raises (telemetry must not break the app).
    """
    settings = get_current_settings()
    if not settings.langwatch_enabled or not settings.langwatch_api_key:
        return False
    try:
        import json
        import time
        import urllib.request

        payload = {
            "trace_id": trace_id,
            "event_type": event_type,
            "metrics": metrics or {},
            "event_details": details or {},
            "timestamp": int(time.time() * 1000),
        }
        req = urllib.request.Request(  # noqa: S310 — fixed https(s) LangWatch endpoint
            f"{settings.langwatch_endpoint.rstrip('/')}/api/track_event",
            data=json.dumps(payload).encode(),
            headers={
                "X-Auth-Token": settings.langwatch_api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — see above
            return bool(200 <= resp.status < 300)
    except Exception:  # noqa: BLE001
        return False


def current_trace_id() -> str | None:
    """The active trace id (32-hex), to tie custom events back to this run. None if no span."""
    try:
        from opentelemetry import trace as _otel

        ctx = _otel.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
    except Exception:  # noqa: BLE001
        return None


def get_current_settings() -> Settings:
    from .config import get_settings

    return get_settings()
