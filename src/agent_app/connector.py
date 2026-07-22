"""Connector register client — resolves observability links at runtime.

The agent's env carries ONE observability setting: ``CONNECTOR_REGISTER_URL``
(per-client, static). Everything else — where traces/events go, with which
credentials — is resolved from the register at runtime, so the platform can
move hosts, rotate keys or swap vendors without touching any agent config.

Contract (v1): ``GET {CONNECTOR_REGISTER_URL}`` — the env URL is the complete
entry point, fetched verbatim — returns a hypermedia document::

    {
      "version": "1",
      "ttl_seconds": 300,
      "links": {
        "traces": {"href": "...", "method": "POST", "headers": {...}},
        "events": {"href": "...", "method": "POST", "headers": {...}}
      }
    }

Client obligations (see docs/observability.md):
  * cache the document for ``ttl_seconds`` (register-declared; default 300);
  * re-fetch on expiry AND on link failure (``invalidate()``);
  * treat ``headers`` as opaque — copied verbatim onto every request;
  * ignore unknown links; an absent link disables that capability;
  * best-effort always: the register being down must never crash or block
    the agent — ``link()`` just returns None and we retry later (throttled).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger("agent_app.connector")

_DEFAULT_TTL_S = 300.0
_FAILURE_RETRY_S = 15.0  # min interval between fetch attempts after a failure
_FETCH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Link:
    """One capability endpoint. ``headers`` are opaque (auth lives there)."""

    href: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)


class ConnectorClient:
    """Cached resolver for the connector document. Thread-safe."""

    def __init__(self, register_url: str) -> None:
        self._register_url = register_url
        self._lock = threading.Lock()
        self._links: dict[str, Link] = {}
        self._expires_at = 0.0  # monotonic; 0 → never fetched
        self._next_attempt_at = 0.0  # failure throttle

    def link(self, name: str) -> Link | None:
        """Current link for a capability, refreshing the document if stale.

        Returns None when the capability is absent or the register is
        unreachable — callers drop the telemetry and move on (best-effort).
        """
        with self._lock:
            now = time.monotonic()
            if now >= self._expires_at and now >= self._next_attempt_at:
                self._refresh(now)
            return self._links.get(name)

    def invalidate(self) -> None:
        """Force a re-fetch on next access (call when a link stops working)."""
        with self._lock:
            self._expires_at = 0.0
            self._next_attempt_at = 0.0

    # -- internals ----------------------------------------------------------

    def _refresh(self, now: float) -> None:
        try:
            # The env URL is the COMPLETE entry point, fetched verbatim — the agent
            # never constructs URLs; the register owns its own URL space entirely.
            req = urllib.request.Request(  # noqa: S310 — operator-configured register URL
                self._register_url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310
                doc = json.load(resp)
            links: dict[str, Link] = {}
            for name, raw in (doc.get("links") or {}).items():
                href = raw.get("href")
                if not isinstance(href, str) or not href:
                    continue
                headers = raw.get("headers") or {}
                links[name] = Link(
                    href=href,
                    method=str(raw.get("method", "POST")),
                    headers={str(k): str(v) for k, v in headers.items()},
                )
            ttl = doc.get("ttl_seconds")
            ttl_s = float(ttl) if isinstance(ttl, int | float) and ttl > 0 else _DEFAULT_TTL_S
            self._links = links
            self._expires_at = now + ttl_s
            logger.info("connector document refreshed: links=%s ttl=%ss", sorted(links), int(ttl_s))
        except Exception as exc:  # noqa: BLE001 — register down must never break the agent
            self._next_attempt_at = now + _FAILURE_RETRY_S
            # Keep serving stale links (if any) until the register answers again.
            self._expires_at = now + _FAILURE_RETRY_S
            logger.warning(
                "connector register unreachable (%s); retrying in %ss", exc, int(_FAILURE_RETRY_S)
            )
