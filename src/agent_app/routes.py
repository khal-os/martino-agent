"""Custom HTTP routes layered on top of AgentOS.

AgentOS already provides 50+ endpoints. We add three:
  * ``/livez``   — liveness (200 while the process is up). Use for the container probe.
  * ``/health``  — readiness (503 when the session DB is unreachable).
  * ``/feedback`` — record end-user 👍/👎 as a LangWatch event tied to a trace.

The health/livez handlers are built by factories (``build_*_route``) so ``main.py``
can bind them to module names and register them; ``register_feedback_route`` wires
the POST handler directly onto the app.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings
from .observability import track_event

HealthRoute = Callable[[], JSONResponse]


def build_health_route(agents: Sequence[Any], settings: Settings) -> HealthRoute:
    """Readiness: 503 when the session store is unreachable (all agents share it)."""

    def health() -> JSONResponse:
        try:
            # Cheap round-trip against the sessions db.
            agents[0].db.get_sessions(limit=1)
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        status = 200 if db_ok else 503
        return JSONResponse(
            {
                "status": "ok" if db_ok else "degraded",
                "version": settings.agent_version,  # single source: _version.py
                "git_sha": settings.git_sha,  # build provenance (unknown locally)
                "environment": settings.environment,
                "agents": [a.id for a in agents],
                "db": db_ok,
            },
            status_code=status,
        )

    return health


def build_livez_route(settings: Settings) -> HealthRoute:
    """Liveness: 200 as long as the process is up. Use THIS for the container
    healthcheck — /health is readiness (503 on DB down) and would restart-loop a
    healthy app during a transient DB outage."""

    def livez() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": settings.agent_version})

    return livez


class FeedbackIn(BaseModel):
    trace_id: str  # from a run response (or observability.current_trace_id())
    positive: bool  # 👍 / 👎
    comment: str | None = None
    score: float | None = None


def register_feedback_route(app: FastAPI, settings: Settings) -> None:
    """Wire ``POST /feedback`` — product signals attached to the producing trace.

    Thumbs, ratings, "converted", "escalated" are how you slice quality by real
    outcomes instead of guessing. Wire your UI's 👍/👎 button here.
    """

    @app.post("/feedback")
    def feedback(body: FeedbackIn) -> JSONResponse:
        if not re.fullmatch(r"[0-9a-f]{32}", body.trace_id):
            return JSONResponse({"error": "invalid trace_id (expected 32-hex)"}, status_code=422)
        ok = track_event(
            body.trace_id,
            "thumbs_up" if body.positive else "thumbs_down",
            metrics={"score": body.score} if body.score is not None else {"vote": 1.0},
            details={"comment": body.comment} if body.comment else {},
        )
        return JSONResponse({"recorded": ok})
