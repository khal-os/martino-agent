"""Observability via the connector — vendor-neutral OTLP transport, unchanged payload.

The agent knows NOTHING about the observability vendor. It emits standard
OTLP-over-HTTP traces and HTTP+JSON events to endpoints resolved at runtime
from the per-client **connector register** (see connector.py). Only the
TRANSPORT is connector-aware — the PAYLOAD is whatever the instrumentation
naturally produces; adapting it to a platform's conventions is the connector
side's job, never the agent's:

**1. Resource / static metadata (every span in the process)** — standard OTel
semconv keys (``service.name`` / ``service.version`` / ...) on the Resource.

**2. Per-request metadata (this run)** — ``enrich_current_trace()`` attaches
user_id, thread_id (session), labels and any custom fields to the auto-created
trace. Called from a pre-hook so it runs on every agent turn. (Session/user
also arrive natively: the Agno instrumentor emits standard ``session.id`` /
``user.id`` span attributes from the run params.)

**3. Token accounting** — the Agno OpenInference instrumentor emits
``llm.token_count.*`` (incl. ``prompt_details.cache_read`` /
``prompt_details.cache_write``) — what the platform prices traces from.

Auto-instrumentation: the Agno OpenInference instrumentor traces every run/LLM/
tool call. Custom spans use a plain OTel tracer (see tools/example_tools.py).

Enable with ``CONNECTOR_REGISTER_URL`` (the only env setting). No-op — and
never crashes the app — when unset or when deps aren't installed.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from .config import Settings
from .connector import ConnectorClient

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExportResult

logger = logging.getLogger("agent_app.observability")

_INITIALIZED = False
_CONNECTOR: ConnectorClient | None = None


def _resource_attributes(settings: Settings) -> dict[str, Any]:
    """OTel Resource attributes — stamped on every span (static, per-process).

    Pure OTel semconv — the agent emits STANDARD names only. Mapping them to
    whatever the observability platform wants is the connector side's job
    (adaptation lives behind the link, never in the agent).
    """
    attrs = {
        # Standard OTel semantic-convention resource keys → nice grouping in any backend.
        "service.name": settings.service_name,
        "service.version": settings.agent_version,  # the agent version (single source)
        "deployment.environment": settings.environment,
        # ADDITIVE custom keys: LangWatch drops semconv `service.version` (and has no
        # instance concept) at ingestion, so version/instance also travel under names
        # the platform passes through verbatim — the module mapper's primary keys.
        "agent.version": settings.agent_version,
        "agent.instance": settings.agent_instance,
        # Handy app-level context (shows up as trace attributes).
        "model.provider": settings.model_provider,
        "model.id": settings.model_id,
    }
    if settings.git_sha != "unknown":
        attrs["vcs.revision"] = settings.git_sha  # build/deploy provenance
    return attrs


class _ConnectorSpanExporter:
    """OTLP exporter whose destination is resolved via the connector register.

    Each batch export asks the ConnectorClient for the current ``traces`` link
    (cached, so this is cheap) and lazily (re)builds the inner OTLP exporter
    when the link changes — host moves and key rotations propagate within the
    register's TTL, with no agent restart. On export failure the document is
    invalidated so the next batch re-resolves immediately.
    """

    def __init__(self, client: ConnectorClient) -> None:
        self._client = client
        self._current: Any = None  # (href, headers-tuple) of the built exporter
        self._inner: Any = None

    def export(self, spans: Any) -> SpanExportResult:
        from opentelemetry.sdk.trace.export import SpanExportResult

        link = self._client.link("traces")
        if link is None:
            return SpanExportResult.FAILURE  # best-effort: batch is dropped
        key = (link.href, tuple(sorted(link.headers.items())))
        if key != self._current:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            self._inner = OTLPSpanExporter(endpoint=link.href, headers=dict(link.headers))
            self._current = key
        result = self._inner.export(spans)
        if result != SpanExportResult.SUCCESS:
            self._client.invalidate()  # maybe the vendor moved — re-resolve next batch
        return result

    def shutdown(self) -> None:
        if self._inner is not None:
            self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def setup_observability(settings: Settings) -> None:
    global _INITIALIZED, _CONNECTOR
    if _INITIALIZED or not settings.connector_register_url:
        return
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        _CONNECTOR = ConnectorClient(settings.connector_register_url)

        provider = TracerProvider(resource=Resource.create(_resource_attributes(settings)))
        provider.add_span_processor(BatchSpanProcessor(_ConnectorSpanExporter(_CONNECTOR)))
        otel_trace.set_tracer_provider(provider)

        try:
            from openinference.instrumentation.agno import AgnoInstrumentor

            AgnoInstrumentor().instrument(tracer_provider=provider)
        except ImportError:
            logger.warning(
                "openinference-instrumentation-agno not installed; "
                "install extras: uv pip install -e '.[observability]'"
            )

        _INITIALIZED = True
        logger.info(
            "observability enabled → register=%s (service=%s env=%s)",
            settings.connector_register_url,
            settings.service_name,
            settings.environment,
        )
    except ImportError:
        logger.warning("opentelemetry-sdk not installed; skipping. Install '.[observability]'.")
    except Exception as exc:  # noqa: BLE001 — never let telemetry break the app
        logger.warning("observability setup failed (%s); continuing without it.", exc)


# Span-attribute keys, UNCHANGED from the agent's original emission — the agent's
# payload is frozen; adapting it to any platform's conventions happens on the
# connector side, never here. (Session/user also arrive natively: the Agno
# instrumentor emits standard ``session.id``/``user.id`` from the run params.)
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

    - ``user_id`` / ``session_id`` / ``customer_id`` use reserved keys the
      platform promotes to trace-level fields, so the UI can group & filter by
      user, conversation (thread) and tenant.
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

    Sets ``ab.experiment`` / ``ab.variant`` / ``ab.variant_version`` on the
    current (auto-instrumented) span. Those become group-by/filter dimensions
    in the observability platform, so you can slice quality, latency, tokens
    and cost **per variant** — which is the whole point: the platform is the
    measurement plane for the experiment.

    Must be called *during* the run (from a pre-hook) so the run's span is
    current — see hooks/pre_hooks.py:tag_experiment. Safe no-op when tracing
    is off.
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
    """Record a custom user event against a trace (connector ``events`` link).

    This is how product signals — 👍/👎 feedback, ratings, "converted",
    "escalated" — get attached to the trace that produced them, so you can
    slice quality by real outcomes. ``trace_id`` comes from the run (see
    ``current_trace_id()``). The destination and credentials come from the
    connector register; absent link → capability off, returns False.

    Returns True on success; never raises (telemetry must not break the app).
    """
    if _CONNECTOR is None:
        return False
    link = _CONNECTOR.link("events")
    if link is None:
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
        req = urllib.request.Request(  # noqa: S310 — register-resolved connector endpoint
            link.href,
            data=json.dumps(payload).encode(),
            headers={**link.headers, "Content-Type": "application/json"},
            method=link.method,
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — see above
            ok = bool(200 <= resp.status < 300)
        if not ok:
            _CONNECTOR.invalidate()
        return ok
    except Exception:  # noqa: BLE001
        _CONNECTOR.invalidate()  # maybe the vendor moved — re-resolve next call
        return False


def current_trace_id() -> str | None:
    """The active trace id (32-hex), to tie custom events back to this run. None if no span."""
    try:
        from opentelemetry import trace as _otel

        ctx = _otel.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
    except Exception:  # noqa: BLE001
        return None
