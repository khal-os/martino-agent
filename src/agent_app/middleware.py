"""HTTP middleware — the Bearer-token gate on protected routes, plus the
per-request channel capture.

Auth posture (house pattern):
  - ``API_KEY`` set            → token required (constant-time compare).
  - ``API_KEY`` unset in dev   → open (a warning is logged at boot).
  - ``API_KEY`` unset elsewhere → deny all protected routes (fail closed).

Attach in ``main.py`` with ``app.add_middleware(BearerAuthMiddleware, settings=...)``.
"""

from __future__ import annotations

import hmac
from contextvars import ContextVar
from typing import NamedTuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from .config import Settings

# ── Per-request channel (X-Channel-* headers) ────────────────────────────────
# Callers may declare which channel a run comes from via headers mirroring the
# observability platform's channel block:
#   X-Channel-Type      browser / web / backoffice / …
#   X-Channel-Version   the caller's deployed version (optional)
#   X-Channel-Instance  the caller's replica/deployment (optional)
# The enrich_trace pre-hook reads them through ``current_request_channel()``
# and stamps them on the trace. Resolution order there: omni run metadata →
# these headers → the CHANNEL env default. A ContextVar (not a global) so
# concurrent requests never see each other's values.


class RequestChannel(NamedTuple):
    type: str | None
    version: str | None
    instance: str | None


_request_channel: ContextVar[RequestChannel | None] = ContextVar(
    "request_channel", default=None
)

_MAX_CHANNEL_LEN = 64  # headers are caller-controlled — keep trace labels sane


def current_request_channel() -> RequestChannel | None:
    """The X-Channel-* values of the request being served, if any were sent."""
    return _request_channel.get()


class ChannelHeaderMiddleware(BaseHTTPMiddleware):
    """Capture the ``X-Channel-*`` headers into the request context."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        def header(name: str) -> str | None:
            return (request.headers.get(name) or "").strip()[:_MAX_CHANNEL_LEN] or None

        channel = RequestChannel(
            type=header("x-channel-type"),
            version=header("x-channel-version"),
            instance=header("x-channel-instance"),
        )
        token = _request_channel.set(channel if any(channel) else None)
        try:
            return await call_next(request)
        finally:
            _request_channel.reset(token)

# Routes reachable without a token. (/livez + /health are liveness/readiness probes.)
PUBLIC_PATHS = ("/", "/livez", "/health", "/docs", "/openapi.json", "/redoc")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <API_KEY>`` on protected routes."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.auth_open = settings.api_key is None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/assets"):
            return await call_next(request)
        if self.auth_open:
            if self.settings.environment == "dev":
                return await call_next(request)
            return JSONResponse({"error": "server auth not configured"}, status_code=401)
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(token, self.settings.api_key or ""):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
