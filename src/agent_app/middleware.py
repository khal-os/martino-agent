"""HTTP middleware — the Bearer-token gate on protected routes.

Auth posture (house pattern):
  - ``API_KEY`` set            → token required (constant-time compare).
  - ``API_KEY`` unset in dev   → open (a warning is logged at boot).
  - ``API_KEY`` unset elsewhere → deny all protected routes (fail closed).

Attach in ``main.py`` with ``app.add_middleware(BearerAuthMiddleware, settings=...)``.
"""

from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from .config import Settings

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
