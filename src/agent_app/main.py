"""AgentOS entrypoint.

AgentOS turns the registered agents into a FastAPI app with 50+ REST endpoints
(runs, sessions, memory, knowledge, health, ...). We expose ``app`` for uvicorn
and add:

  * a Bearer-token gate on every non-public route (house pattern), and
  * a DB-aware ``/health`` that returns 503 when the database is down.

Run:  uvicorn agent_app.main:app --host 0.0.0.0 --port 8888 --workers 2
Or:   python -m agent_app.main   (uses AgentOS.serve, single process, hot for dev)

Docs — Agno AgentOS: https://docs.agno.com/agent-os/introduction
"""

from __future__ import annotations

from agno.os import AgentOS
from fastapi import FastAPI
from fastapi.routing import APIRoute

from .agents import get_agents
from .config import get_settings
from .experiments import build_experiment_factories, register_experiment_routes
from .log import configure_logging, get_logger
from .middleware import BearerAuthMiddleware
from .observability import setup_observability
from .omni import register_omni_route
from .routes import build_health_route, build_livez_route, register_feedback_route

settings = get_settings()
# Configure structured logging FIRST — before any log line is emitted below.
configure_logging(json_logs=settings.log_json, level=settings.log_level)
logger = get_logger("agent_app")

# Auth posture: an unset API_KEY means no token gate. That's convenient for local
# dev but dangerous anywhere else, so it's only allowed in ENVIRONMENT=dev.
AUTH_OPEN = settings.api_key is None
if AUTH_OPEN:
    if settings.environment == "dev":
        logger.warning("API_KEY unset — auth is OPEN (dev only). Set API_KEY before staging/prod.")
    else:
        logger.error(
            "API_KEY unset in ENVIRONMENT=%s — protected routes will DENY all requests. "
            "Set API_KEY.",
            settings.environment,
        )

# Wire tracing BEFORE building agents so the instrumentor patches Agno.
setup_observability(settings)

AGENTS = list(get_agents())
# A/B experiments served as native agents: each becomes an AgentFactory reachable
# at POST /agents/{experiment_key}/runs with full native features (media, streaming,
# sessions) — sticky variant picked per request. See experiments/factory.py.
EXPERIMENT_FACTORIES = build_experiment_factories(settings)
agent_os = AgentOS(
    id=f"{settings.agent_id}-os",
    agents=[*AGENTS, *EXPERIMENT_FACTORIES],
    # Agno native tracing: every run/LLM-call/tool-call is stored as spans in the
    # agents' own db (agno_traces / agno_spans tables) — free, offline, no extra
    # infra, browsable via the AgentOS API / os.agno.com. Complements the
    # connector-resolved OTLP export enabled separately via CONNECTOR_REGISTER_URL.
    tracing=True,
)
app: FastAPI = agent_os.get_app()

# Bearer-token gate on protected routes (see middleware.py for the auth posture).
app.add_middleware(BearerAuthMiddleware, settings=settings)

# Custom probes + feedback (see routes.py). health/livez are bound at module level
# so they're importable/testable; both are inserted at the FRONT of the router so
# they win over AgentOS's own liveness-only /health (FastAPI takes the first match).
health = build_health_route(AGENTS, settings)
livez = build_livez_route(settings)
app.router.routes.insert(0, APIRoute("/health", health, methods=["GET"]))
app.router.routes.insert(0, APIRoute("/livez", livez, methods=["GET"]))
register_feedback_route(app, settings)

# A/B experiments: sticky variant routing + remote traffic control + monitoring,
# with the serving arm stamped on each trace (ab.variant) for LangWatch slicing.
# See experiments/ and docs/ab-testing.md.
register_experiment_routes(app, settings)

# Omnichannel: POST /omni/webhook lets Automagik Omni (WhatsApp/Slack/…) dispatch
# channel messages to the agent — baseline, or routed through an A/B experiment
# (OMNI_EXPERIMENT). See omni.py and docs/omni.md.
register_omni_route(app, settings)


def main() -> None:
    agent_os.serve(app="agent_app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
