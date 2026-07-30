"""Connector register client — capability resolution at runtime (khal platform).

The agent's env carries TWO observability settings: ``CONNECTOR_REGISTER_URL``
(the khal connector-register base URL) and ``M2M_TOKEN`` (the agent's M2M
identity token, sent as Bearer — issued by the agent-register when the FDE
registers the agent; a base64url dev-claims token locally). Everything else
— where traces go, with
which credentials — is resolved from the register at runtime, so the platform
can move hosts, rotate keys or swap vendors without touching agent config.

Contract: ``POST {CONNECTOR_REGISTER_URL}/connections`` with an *intent* —
the capability the agent needs plus how it knows how to speak::

    {"capability": {"signal": "monitoring.trace", "operation": "write"},
     "binding": {"transport": "http", "protocol": "otlp", "encoding": "protobuf"}}

→ ``{connectorId, connectsTo, resolvedUrl, ttlSeconds, chosenBinding,
credential?: {placement, name, scheme, value}}``. The agent never asks for a
connector by id; it states what it needs and receives a resolved connection
with a short-lived credential. After that the agent talks straight to the
connector — the register is never in the event path.

Client obligations:
  * cache each resolution for its ``ttlSeconds`` (credential freshness);
  * re-resolve on expiry AND on link failure (``invalidate()``);
  * apply the credential where the response says (header or query) — the
    register tells *where*, the client applies it;
  * a capability with no connector (404 ``no_connector_for_capability``) is
    OFF — negative-cached so the register isn't hammered;
  * best-effort always: the register being down must never crash or block
    the agent — ``link()`` just returns None and we retry later (throttled).

Link names map to intents via ``_INTENTS``. ``events`` has no signal in the
platform vocabulary yet — the capability is off until one exists (the
``events`` link resolves to None without any HTTP call).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger("agent_app.connector")

_DEFAULT_TTL_S = 300.0
_FAILURE_RETRY_S = 15.0  # min interval between attempts after a failure
_FETCH_TIMEOUT_S = 5.0

# Link name → resolution intent. The agent states WHAT it needs (capability)
# and HOW it can speak (binding); the register picks the connector.
# ``events``: intentionally absent — the platform vocabulary has no event
# signal yet (only monitoring.trace / billing.usage); capability off.
_INTENTS: Final[dict[str, dict[str, dict[str, str]]]] = {
    "traces": {
        "capability": {"signal": "monitoring.trace", "operation": "write"},
        "binding": {"transport": "http", "protocol": "otlp", "encoding": "protobuf"},
    },
}


@dataclass(frozen=True)
class Link:
    """One resolved capability endpoint. ``headers`` carry the credential."""

    href: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _CacheEntry:
    link: Link | None  # None = capability off (negative cache)
    expires_at: float


def _credential_headers_and_href(resolution: dict[str, Any]) -> tuple[dict[str, str], str]:
    """Apply the credential where the register says it belongs.

    * ``header`` placement → a request header. ``Bearer``/``Basic`` prefix the
      value with the scheme (standard Authorization forms); ``ApiKey`` sends
      the bare value — vendor headers like ``X-Api-Key`` take the raw key,
      and ``ApiKey <v>`` is not a standard Authorization scheme.
    * ``query`` placement → the parameter is appended to the resolved URL.
    * no credential block → the connector needs none.
    """
    href = str(resolution["resolvedUrl"])
    credential = resolution.get("credential")
    if not isinstance(credential, dict):
        return {}, href
    name = str(credential.get("name", ""))
    value = str(credential.get("value", ""))
    scheme = str(credential.get("scheme", ""))
    if not name or not value:
        return {}, href
    if credential.get("placement") == "query":
        parts = urllib.parse.urlsplit(href)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query.append((name, value))
        return {}, urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    header_value = value if scheme == "ApiKey" else f"{scheme} {value}".strip()
    return {name: header_value}, href


class ConnectorClient:
    """Cached per-capability resolver against the connector register. Thread-safe."""

    def __init__(self, register_url: str, token: str) -> None:
        self._connections_url = register_url.rstrip("/") + "/connections"
        self._token = token
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._next_attempt_at: dict[str, float] = {}  # per-link failure throttle
        self._off_logged: set[str] = set()

    def link(self, name: str) -> Link | None:
        """Current resolved link for a capability, re-resolving when stale.

        Returns None when the capability is off (no intent mapped, no
        connector registered) or the register is unreachable — callers drop
        the telemetry and move on (best-effort).
        """
        intent = _INTENTS.get(name)
        if intent is None:
            if name not in self._off_logged:
                self._off_logged.add(name)
                logger.info("capability '%s' has no signal on the platform yet; disabled", name)
            return None
        with self._lock:
            now = time.monotonic()
            entry = self._cache.get(name)
            if entry is not None and now < entry.expires_at:
                return entry.link
            if now < self._next_attempt_at.get(name, 0.0):
                return entry.link if entry else None  # throttled: serve stale
            return self._resolve(name, intent, now)

    def invalidate(self) -> None:
        """Force re-resolution on next access (call when a link stops working)."""
        with self._lock:
            self._cache.clear()
            self._next_attempt_at.clear()

    # -- internals ----------------------------------------------------------

    def _resolve(
        self, name: str, intent: dict[str, dict[str, str]], now: float
    ) -> Link | None:
        try:
            req = urllib.request.Request(  # noqa: S310 — operator-configured register URL
                self._connections_url,
                data=json.dumps(intent).encode(),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310
                resolution = json.load(resp)
            if not isinstance(resolution, dict) or "resolvedUrl" not in resolution:
                raise ValueError("malformed resolution document")
            headers, href = _credential_headers_and_href(resolution)
            ttl = resolution.get("ttlSeconds")
            ttl_s = float(ttl) if isinstance(ttl, int | float) and ttl > 0 else _DEFAULT_TTL_S
            link = Link(href=href, method="POST", headers=headers)
            self._cache[name] = _CacheEntry(link=link, expires_at=now + ttl_s)
            logger.info(
                "capability '%s' resolved: connector=%s ttl=%ss",
                name,
                resolution.get("connectorId"),
                int(ttl_s),
            )
            return link
        except urllib.error.HTTPError as exc:
            code = _problem_code(exc)
            if exc.code == 404 and code == "no_connector_for_capability":
                # Capability off: nothing serves this signal — negative-cache
                # so the register isn't hammered; re-check after the TTL.
                self._cache[name] = _CacheEntry(link=None, expires_at=now + _DEFAULT_TTL_S)
                logger.info("capability '%s': no connector registered; disabled for now", name)
                return None
            self._next_attempt_at[name] = now + _FAILURE_RETRY_S
            logger.warning(
                "connector resolution rejected for '%s': HTTP %s (%s); retrying in %ss",
                name,
                exc.code,
                code or "no problem code",
                int(_FAILURE_RETRY_S),
            )
        except Exception as exc:  # noqa: BLE001 — register down must never break the agent
            self._next_attempt_at[name] = now + _FAILURE_RETRY_S
            logger.warning(
                "connector register unreachable for '%s' (%s); retrying in %ss",
                name,
                exc,
                int(_FAILURE_RETRY_S),
            )
        stale = self._cache.get(name)
        return stale.link if stale else None  # keep serving stale until it answers


def _problem_code(exc: urllib.error.HTTPError) -> str | None:
    """Best-effort ``code`` from an RFC 9457 problem+json body."""
    try:
        body = json.loads(exc.read().decode())
        code = body.get("code") if isinstance(body, dict) else None
        return str(code) if code is not None else None
    except Exception:  # noqa: BLE001 — diagnostics only
        return None
