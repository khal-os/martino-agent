"""Observability wiring — offline. No LangWatch server, no network.

Proves the enrichment is safe (never raises) and that resource metadata is
assembled richly, so traces aren't `service.name: unknown_service`.
"""

from agent_app import config
from agent_app.observability import _base_attributes, enrich_current_trace


def test_base_attributes_are_rich(monkeypatch):
    from agent_app import __version__

    monkeypatch.setenv("OTEL_SERVICE_NAME", "my-svc")
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("GIT_SHA", "abc123")
    config.get_settings.cache_clear()
    attrs = _base_attributes(config.get_settings())
    assert attrs["service.name"] == "my-svc"  # fixes unknown_service
    assert attrs["service.version"] == __version__  # single source: _version.py
    assert attrs["deployment.environment"] == "prod"
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
