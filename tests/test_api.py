"""API surface tests — Bearer auth posture, liveness/readiness, feedback validation.

These reload agent_app.main under different env so the module-level auth posture
(AUTH_OPEN) is recomputed. No network: agents build with a dummy key (conftest),
LangWatch is disabled, and no run endpoint is exercised.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from agent_app import config

PROTECTED = "/does-not-exist"  # any non-public path: the middleware runs before routing


def build(monkeypatch, *, api_key=None, environment="dev"):
    if api_key is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", api_key)
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("LANGWATCH_ENABLED", "0")
    config.get_settings.cache_clear()
    import agent_app.main as main

    importlib.reload(main)
    return main, TestClient(main.app)


# --- Bearer auth matrix ----------------------------------------------------


def test_auth_open_in_dev_when_key_unset(monkeypatch):
    _, client = build(monkeypatch, api_key=None, environment="dev")
    # Open → middleware lets it through → 404 (route missing), NOT 401.
    assert client.get(PROTECTED).status_code == 404


def test_auth_fails_closed_in_prod_when_key_unset(monkeypatch):
    _, client = build(monkeypatch, api_key=None, environment="prod")
    assert client.get(PROTECTED).status_code == 401


def test_auth_requires_token_when_key_set(monkeypatch):
    _, client = build(monkeypatch, api_key="s3cret", environment="dev")
    assert client.get(PROTECTED).status_code == 401  # no token
    assert client.get(PROTECTED, headers={"Authorization": "Bearer wrong"}).status_code == 401
    # correct token → passes the middleware → 404 (route missing), not 401
    assert client.get(PROTECTED, headers={"Authorization": "Bearer s3cret"}).status_code == 404


def test_public_paths_never_need_a_token(monkeypatch):
    _, client = build(monkeypatch, api_key="s3cret", environment="prod")
    for path in ("/livez", "/health"):
        assert client.get(path).status_code in (200, 503)  # reachable w/o token


# --- Liveness vs readiness -------------------------------------------------


def test_livez_is_always_ok(monkeypatch):
    _, client = build(monkeypatch, api_key=None)
    r = client.get("/livez")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_health_degraded_on_db_error(monkeypatch):
    main, client = build(monkeypatch, api_key=None)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(main.AGENTS[0].db, "get_sessions", boom, raising=False)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["db"] is False


# --- Feedback validation ---------------------------------------------------


def test_feedback_rejects_bad_trace_id(monkeypatch):
    _, client = build(monkeypatch, api_key=None)
    r = client.post("/feedback", json={"trace_id": "not-hex", "positive": True})
    assert r.status_code == 422


def test_feedback_accepts_valid_trace_id_noop_when_disabled(monkeypatch):
    _, client = build(monkeypatch, api_key=None)  # LANGWATCH_ENABLED=0 → track_event no-ops
    r = client.post("/feedback", json={"trace_id": "0" * 32, "positive": False, "comment": "meh"})
    assert r.status_code == 200 and r.json()["recorded"] is False


@pytest.fixture(autouse=True)
def _restore_main():
    """Leave agent_app.main in its default (dev, no key, no telemetry) state.

    Force LANGWATCH off on the restoring reload so a real `.env` (LANGWATCH_ENABLED=1
    + a stale key) can't make the reloaded module fire OTel exports during teardown.
    """
    import os

    yield
    import agent_app.main as main

    os.environ["LANGWATCH_ENABLED"] = "0"
    config.get_settings.cache_clear()
    importlib.reload(main)
