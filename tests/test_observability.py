"""Observability wiring — offline. No register, no LangWatch, no network.

Proves the enrichment is safe (never raises), the resource metadata is
assembled richly (payload UNCHANGED — pure semconv, no vendor/contract keys),
and the ConnectorClient honours the document TTL / failure semantics.
"""

import json

from agent_app import config
from agent_app.connector import ConnectorClient
from agent_app.observability import _resource_attributes, enrich_current_trace


def test_resource_attributes_are_rich(monkeypatch):
    from agent_app import __version__

    monkeypatch.setenv("OTEL_SERVICE_NAME", "my-svc")
    monkeypatch.setenv("AGENT_INSTANCE", "replica-7")
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("GIT_SHA", "abc123")
    # The developer's own .env may set these (loaded at import) — isolate.
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.delenv("SUBDOMAIN", raising=False)
    config.get_settings.cache_clear()
    attrs = _resource_attributes(config.get_settings())
    assert attrs["service.name"] == "my-svc"  # fixes unknown_service
    assert attrs["service.version"] == __version__  # single source: _version.py
    assert attrs["deployment.environment"] == "prod"
    # Additive keys the platform passes through (semconv version is dropped there):
    assert attrs["agent.version"] == __version__
    assert attrs["agent.instance"] == "replica-7"
    assert attrs["vcs.revision"] == "abc123"  # build provenance
    assert attrs["model.provider"] and attrs["model.id"]
    # domain/subdomain unset → keys absent (never empty strings).
    assert "domain" not in attrs
    assert "subdomain" not in attrs
    config.get_settings.cache_clear()


def test_resource_attributes_include_domain_scope(monkeypatch):
    monkeypatch.setenv("DOMAIN", "varejo")
    monkeypatch.setenv("SUBDOMAIN", "loja-sp")
    config.get_settings.cache_clear()
    attrs = _resource_attributes(config.get_settings())
    # Bare keys on purpose — the platform module's trace-filter keys.
    assert attrs["domain"] == "varejo"
    assert attrs["subdomain"] == "loja-sp"
    config.get_settings.cache_clear()


def test_enrich_is_safe_noop_without_active_span():
    # No recording span in a plain test → must not raise, must do nothing.
    enrich_current_trace(user_id="u1", session_id="s1", metadata={"turn": 3, "none": None})


def test_enrich_sets_attributes_on_recording_span(monkeypatch):
    """When a span IS recording, reserved + app.* attributes are set on it."""
    captured = {}

    class FakeSpan:
        def is_recording(self):
            return True

        def set_attribute(self, k, v):
            captured[k] = v

    import opentelemetry.trace as otel

    monkeypatch.setattr(otel, "get_current_span", lambda: FakeSpan())
    enrich_current_trace(
        user_id="user-1",
        session_id="thread-1",
        customer_id="tenant-9",
        channel="whatsapp",
        channel_version="3.2.0",
        channel_instance="omni-wa-1",
        metadata={"turn": 2, "skipme": None},
    )
    assert captured["langwatch.user.id"] == "user-1"
    assert captured["langwatch.thread.id"] == "thread-1"
    assert captured["langwatch.customer.id"] == "tenant-9"
    # Channel contract keys are BARE (not app.*) — the platform reads them.
    assert captured["channel"] == "whatsapp"
    assert captured["channel.version"] == "3.2.0"
    assert captured["channel.instance"] == "omni-wa-1"
    assert captured["app.turn"] == 2
    assert "app.skipme" not in captured  # None values are dropped
    assert "app.channel" not in captured


# ── ConnectorClient ──────────────────────────────────────────────────────────


def _resolution(**overrides):
    """A khal /connections resolution document (Bearer header credential)."""
    doc = {
        "connectorId": "langwatch-cliente",
        "connectsTo": "monitoring",
        "resolvedUrl": "http://lw:5562/api/otel/v1/traces",
        "ttlSeconds": 900,
        "chosenBinding": {
            "transport": "http",
            "protocol": "otlp",
            "encoding": "protobuf",
            "endpoint": "http://lw:5562/api/otel/v1/traces",
            "auth": {"placement": "header", "name": "authorization", "scheme": "Bearer"},
        },
        "credential": {
            "placement": "header",
            "name": "authorization",
            "scheme": "Bearer",
            "value": "sk-real",
        },
    }
    doc.update(overrides)
    return doc


def _capturing_urlopen(responses, calls):
    """Fake urlopen: records each Request into `calls`, pops from `responses`.

    Each response is a dict (served as JSON) or an Exception (raised).
    """
    import contextlib
    import io

    @contextlib.contextmanager
    def opener(req, timeout=None):
        calls.append(req)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        yield io.BytesIO(json.dumps(item).encode())

    return opener


def _http_error(status, code):
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "http://register.local/connections",
        status,
        "err",
        None,  # type: ignore[arg-type]
        io.BytesIO(json.dumps({"status": status, "code": code}).encode()),
    )


def test_connector_client_resolves_traces_via_connections(monkeypatch):
    import agent_app.connector as connector_mod

    calls = []
    monkeypatch.setattr(
        connector_mod.urllib.request, "urlopen", _capturing_urlopen([_resolution()], calls)
    )
    client = ConnectorClient("http://catalog.local", "tkn")
    link = client.link("traces")

    # The intent went to POST /connections with the catalog token — the full
    # usage-intent tuple (incl. protocolVersion).
    assert calls[0].full_url == "http://catalog.local/connections"
    assert calls[0].get_header("Authorization") == "Bearer tkn"
    body = json.loads(calls[0].data.decode())
    assert body["capability"] == {"signal": "monitoring.trace", "operation": "write"}
    assert body["binding"] == {
        "transport": "http",
        "protocol": "otlp",
        "protocolVersion": "1.0",
        "encoding": "protobuf",
    }

    # The link is the resolved endpoint with the credential applied.
    assert link is not None
    assert link.href == "http://lw:5562/api/otel/v1/traces"
    assert link.headers == {"authorization": "Bearer sk-real"}


def test_connector_client_apikey_scheme_sends_bare_value(monkeypatch):
    import agent_app.connector as connector_mod

    doc = _resolution(
        credential={"placement": "header", "name": "X-Api-Key", "scheme": "ApiKey", "value": "k1"}
    )
    monkeypatch.setattr(
        connector_mod.urllib.request, "urlopen", _capturing_urlopen([doc], [])
    )
    link = ConnectorClient("http://register.local", "tkn").link("traces")
    assert link is not None
    assert link.headers == {"X-Api-Key": "k1"}  # no "ApiKey " prefix


def test_connector_client_query_placement_appends_param(monkeypatch):
    import agent_app.connector as connector_mod

    doc = _resolution(
        resolvedUrl="http://lw:5562/v1/traces?a=1",
        credential={"placement": "query", "name": "api_key", "scheme": "ApiKey", "value": "k 1"},
    )
    monkeypatch.setattr(
        connector_mod.urllib.request, "urlopen", _capturing_urlopen([doc], [])
    )
    link = ConnectorClient("http://register.local", "tkn").link("traces")
    assert link is not None
    assert link.href == "http://lw:5562/v1/traces?a=1&api_key=k+1"
    assert link.headers == {}


def test_connector_client_no_credential_no_headers(monkeypatch):
    import agent_app.connector as connector_mod

    doc = _resolution()
    del doc["credential"]
    monkeypatch.setattr(
        connector_mod.urllib.request, "urlopen", _capturing_urlopen([doc], [])
    )
    link = ConnectorClient("http://register.local", "tkn").link("traces")
    assert link is not None
    assert link.headers == {}


def test_connector_client_events_capability_off_without_http(monkeypatch):
    import agent_app.connector as connector_mod

    calls = []
    monkeypatch.setattr(
        connector_mod.urllib.request, "urlopen", _capturing_urlopen([], calls)
    )
    client = ConnectorClient("http://register.local", "tkn")
    # No event signal exists on the platform yet → off, and no request is made.
    assert client.link("events") is None
    assert client.link("events") is None
    assert calls == []


def test_connector_client_404_is_negative_cached(monkeypatch):
    import agent_app.connector as connector_mod

    calls = []
    monkeypatch.setattr(
        connector_mod.urllib.request,
        "urlopen",
        _capturing_urlopen([_http_error(404, "no_connector_for_capability")], calls),
    )
    client = ConnectorClient("http://register.local", "tkn")
    assert client.link("traces") is None
    assert client.link("traces") is None  # served from the negative cache
    assert len(calls) == 1


def test_connector_client_auth_error_throttles_not_crashes(monkeypatch):
    import agent_app.connector as connector_mod

    calls = []
    monkeypatch.setattr(
        connector_mod.urllib.request,
        "urlopen",
        _capturing_urlopen([_http_error(403, "insufficient_scope")], calls),
    )
    client = ConnectorClient("http://register.local", "tkn")
    assert client.link("traces") is None  # best-effort: no link, no crash
    assert client.link("traces") is None  # throttled: no second attempt yet
    assert len(calls) == 1


def test_connector_client_register_down_is_none_not_crash(monkeypatch):
    import agent_app.connector as connector_mod

    def boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(connector_mod.urllib.request, "urlopen", boom)
    client = ConnectorClient("http://register.local", "tkn")
    assert client.link("traces") is None  # best-effort: no link, no crash


def test_connector_client_invalidate_forces_reresolution(monkeypatch):
    import agent_app.connector as connector_mod

    responses = [
        _resolution(resolvedUrl="http://old/v1/traces"),
        _resolution(resolvedUrl="http://new/v1/traces"),
    ]
    monkeypatch.setattr(
        connector_mod.urllib.request, "urlopen", _capturing_urlopen(responses, [])
    )
    client = ConnectorClient("http://register.local", "tkn")
    assert client.link("traces").href == "http://old/v1/traces"
    assert client.link("traces").href == "http://old/v1/traces"  # cached (ttlSeconds)
    client.invalidate()  # e.g. exports started failing — connector moved
    assert client.link("traces").href == "http://new/v1/traces"


def test_setup_observability_url_without_token_stays_off(monkeypatch):
    from agent_app import config
    from agent_app.observability import setup_observability

    monkeypatch.setenv("CONNECTOR_CATALOG_URL", "http://catalog.local")
    monkeypatch.delenv("M2M_TOKEN", raising=False)
    config.get_settings.cache_clear()

    import agent_app.observability as obs

    setup_observability(config.get_settings())
    assert obs._INITIALIZED is False  # tracing off, no crash
    config.get_settings.cache_clear()


def test_connector_catalog_url_falls_back_to_legacy_env(monkeypatch):
    """CONNECTOR_REGISTER_URL (pre-Catalog-rename) still turns tracing on."""
    from agent_app import config

    monkeypatch.delenv("CONNECTOR_CATALOG_URL", raising=False)
    monkeypatch.setenv("CONNECTOR_REGISTER_URL", "http://legacy.local")
    config.get_settings.cache_clear()

    assert config.get_settings().connector_catalog_url == "http://legacy.local"
    config.get_settings.cache_clear()
