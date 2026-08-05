"""Omni webhook adapter — payload parsing, dispatch (baseline + A/B), auth.

Offline: the agent is stubbed, so no model calls. Verifies the template speaks
Automagik Omni's webhook-provider contract (accepts its payload, returns
{"reply": ...}) and that A/B routing tags the run.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from agent_app import config


def _build(monkeypatch, tmp_path, *, api_key=None, experiment=None):
    if api_key is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", api_key)
    if experiment is None:
        monkeypatch.delenv("OMNI_EXPERIMENT", raising=False)
    else:
        monkeypatch.setenv("OMNI_EXPERIMENT", experiment)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("CONNECTOR_CATALOG_URL", raising=False)
    monkeypatch.delenv("CONNECTOR_REGISTER_URL", raising=False)
    monkeypatch.delenv("TRACES_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("EXPERIMENTS_STORE_PATH", str(tmp_path / "alloc.json"))
    config.get_settings.cache_clear()
    from agent_app.experiments import store as store_mod

    store_mod.get_store.cache_clear()
    import agent_app.main as main

    importlib.reload(main)
    return main, TestClient(main.app)


class _Result:
    content = "stubbed reply"
    run_id = "run-1"


def _stub_agent(monkeypatch):
    captured = {}

    class _Agent:
        async def arun(self, **kwargs):
            captured.update(kwargs)
            return _Result()

    import agent_app.omni as omni_mod

    monkeypatch.setattr(omni_mod, "get_agent", lambda _id: _Agent())
    return captured


def _payload(text="how much is milk?", sender="5511999", chat="chat-1"):
    return {
        "event": {"id": "e1", "type": "message", "timestamp": 0},
        "instance": {"id": "i1", "channelType": "whatsapp"},
        "chat": {"id": chat},
        "sender": {"id": sender, "name": "Alice"},
        "content": {"text": text},
        "traceId": "t1",
    }


def test_baseline_dispatch_returns_reply(monkeypatch, tmp_path):
    _, client = _build(monkeypatch, tmp_path)
    captured = _stub_agent(monkeypatch)
    r = client.post("/omni/webhook", json=_payload())
    assert r.status_code == 200
    assert r.json() == {"reply": "stubbed reply"}
    # sender → user_id, chat → session_id
    assert captured["user_id"] == "5511999"
    assert captured["session_id"] == "chat-1"
    # baseline mode carries no A/B tags
    assert "ab_variant" not in captured["metadata"]
    assert captured["metadata"]["channel"] == "whatsapp"
    # instance id → channel_instance (the omni deployment that served it)
    assert captured["metadata"]["channel_instance"] == "i1"


def test_ab_mode_tags_the_run(monkeypatch, tmp_path):
    _, client = _build(monkeypatch, tmp_path, experiment="assistant-tone")
    captured = _stub_agent(monkeypatch)
    r = client.post("/omni/webhook", json=_payload())
    assert r.status_code == 200
    meta = captured["metadata"]
    assert meta["ab_experiment"] == "assistant-tone"
    assert meta["ab_variant"] in ("A", "B")


def test_empty_text_is_noop(monkeypatch, tmp_path):
    _, client = _build(monkeypatch, tmp_path)
    _stub_agent(monkeypatch)
    r = client.post("/omni/webhook", json=_payload(text="   "))
    assert r.status_code == 200
    assert r.json() == {"reply": ""}


def test_webhook_requires_token_when_api_key_set(monkeypatch, tmp_path):
    # Omni sends Authorization: Bearer <provider apiKey> == the app API_KEY.
    _, client = _build(monkeypatch, tmp_path, api_key="s3cret")
    _stub_agent(monkeypatch)
    assert client.post("/omni/webhook", json=_payload()).status_code == 401  # no token
    ok = client.post("/omni/webhook", json=_payload(), headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_ignores_unknown_payload_fields(monkeypatch, tmp_path):
    _, client = _build(monkeypatch, tmp_path)
    _stub_agent(monkeypatch)
    payload = _payload()
    payload["executionContext"] = {"anything": 1}
    payload["replyEndpoint"] = "POST /api/v2/messages/send"
    r = client.post("/omni/webhook", json=payload)
    assert r.status_code == 200


@pytest.fixture(autouse=True)
def _restore_main():
    import os

    yield
    os.environ.pop("CONNECTOR_CATALOG_URL", None)
    os.environ.pop("CONNECTOR_REGISTER_URL", None)
    os.environ.pop("TRACES_OTLP_ENDPOINT", None)
    config.get_settings.cache_clear()
    import agent_app.main as main

    importlib.reload(main)
