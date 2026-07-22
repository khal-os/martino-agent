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
        metadata={"turn": 2, "channel": "web", "skipme": None},
    )
    assert captured["langwatch.user.id"] == "user-1"
    assert captured["langwatch.thread.id"] == "thread-1"
    assert captured["langwatch.customer.id"] == "tenant-9"
    assert captured["app.turn"] == 2
    assert captured["app.channel"] == "web"
    assert "app.skipme" not in captured  # None values are dropped


# ── ConnectorClient ──────────────────────────────────────────────────────────


def _fake_urlopen(doc):
    """Patchable stand-in for urllib.request.urlopen returning `doc` as JSON."""
    import contextlib
    import io

    @contextlib.contextmanager
    def opener(req, timeout=None):
        yield io.BytesIO(json.dumps(doc).encode())

    return opener


def test_connector_client_resolves_links(monkeypatch):
    import agent_app.connector as connector_mod

    doc = {
        "version": "1",
        "ttl_seconds": 300,
        "links": {
            "traces": {
                "href": "http://lw:5560/api/otel/v1/traces",
                "headers": {"Authorization": "Bearer k"},
            },
            "events": {"href": "http://lw:5560/api/track_event", "headers": {"X-Auth-Token": "k"}},
            "future-capability": {"href": "http://elsewhere/x"},
        },
    }
    monkeypatch.setattr(connector_mod.urllib.request, "urlopen", _fake_urlopen(doc))
    client = ConnectorClient("http://register.local")
    traces = client.link("traces")
    assert traces is not None
    assert traces.href == "http://lw:5560/api/otel/v1/traces"
    assert traces.headers == {"Authorization": "Bearer k"}
    # Unknown links are exposed but never break anything; absent ones are None.
    assert client.link("future-capability") is not None
    assert client.link("nope") is None


def test_connector_client_register_down_is_none_not_crash(monkeypatch):
    import agent_app.connector as connector_mod

    def boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(connector_mod.urllib.request, "urlopen", boom)
    client = ConnectorClient("http://register.local")
    assert client.link("traces") is None  # best-effort: no link, no crash


def test_connector_client_invalidate_forces_refetch(monkeypatch):
    import agent_app.connector as connector_mod

    docs = iter(
        [
            {"ttl_seconds": 3600, "links": {"traces": {"href": "http://old/v1/traces"}}},
            {"ttl_seconds": 3600, "links": {"traces": {"href": "http://new/v1/traces"}}},
        ]
    )

    import contextlib
    import io

    @contextlib.contextmanager
    def opener(req, timeout=None):
        yield io.BytesIO(json.dumps(next(docs)).encode())

    monkeypatch.setattr(connector_mod.urllib.request, "urlopen", opener)
    client = ConnectorClient("http://register.local")
    assert client.link("traces").href == "http://old/v1/traces"
    assert client.link("traces").href == "http://old/v1/traces"  # cached (TTL not expired)
    client.invalidate()  # e.g. exports started failing — vendor moved
    assert client.link("traces").href == "http://new/v1/traces"
