"""Automagik Omni webhook adapter — connect the agent to WhatsApp/Slack/… channels.

[Automagik Omni](https://github.com/automagik-dev/omni) is an omnichannel messaging
hub: it receives messages from channels (WhatsApp, Discord, Slack, Telegram…) and
dispatches them to an agent backend registered as a **webhook provider**. Omni
POSTs a trigger event to our ``base_url``; in *round-trip* mode it waits for the
reply and sends it back to the channel.

This module exposes ``POST /omni/webhook`` speaking that contract, in two modes:

- **baseline** — every message runs one agent (``OMNI_AGENT_ID``, defaults to the
  primary agent).
- **A/B** — set ``OMNI_EXPERIMENT`` to an experiment key and channel messages are
  routed through it (see experiments/). Bucketing is sticky per sender, so e.g.
  each WhatsApp number consistently gets the same arm and every turn is stamped
  with ``ab.variant`` for LangWatch — real omnichannel A/B, no extra code.

Auth: the route sits behind the app's Bearer gate (middleware.py). Register the
Omni provider with ``apiKey`` = this app's ``API_KEY`` and Omni's
``Authorization: Bearer`` header passes the gate. See docs/omni.md.

The Omni webhook payload (fields we use): ``content.text``, ``sender.id`` (→ user),
``chat.id`` (→ session), ``instance.channelType``, ``traceId``. We ignore the rest.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .agents import get_agent
from .config import Settings
from .experiments import assign_variant, get_experiment, get_store, resolve_unit_id


class _Content(BaseModel):
    text: str | None = None
    emoji: str | None = None


class _Sender(BaseModel):
    id: str | None = None
    name: str | None = None


class _Chat(BaseModel):
    id: str | None = None


class _Instance(BaseModel):
    channelType: str | None = None  # noqa: N815 — mirrors Omni's JSON field name


class OmniWebhookIn(BaseModel):
    """Subset of Omni's ``WebhookPayload`` (extra fields ignored)."""

    model_config = ConfigDict(extra="ignore")

    content: _Content = Field(default_factory=_Content)
    sender: _Sender = Field(default_factory=_Sender)
    chat: _Chat = Field(default_factory=_Chat)
    instance: _Instance = Field(default_factory=_Instance)
    traceId: str | None = None  # noqa: N815 — mirrors Omni's JSON field name


def register_omni_route(app: FastAPI, settings: Settings) -> None:
    """Wire ``POST /omni/webhook`` — Omni's webhook provider endpoint."""
    store = get_store(settings.experiments_store_path)

    @app.post("/omni/webhook")
    async def omni_webhook(body: OmniWebhookIn) -> JSONResponse:
        text = (body.content.text or "").strip()
        if not text:
            # Reaction, media-only, or empty event → nothing to answer.
            return JSONResponse({"reply": ""})

        user_id = body.sender.id
        session_id = body.chat.id or user_id
        metadata: dict[str, Any] = {
            "channel": body.instance.channelType,
            "omni_trace_id": body.traceId,
        }

        # A/B mode: route through the configured experiment (sticky per sender).
        # The tag_experiment pre-hook stamps ab.* on the trace from this metadata.
        agent_id = settings.omni_agent_id
        experiment_key = settings.omni_experiment
        if experiment_key:
            try:
                experiment = get_experiment(experiment_key)
            except KeyError:
                experiment = None
            if experiment is not None:
                unit_id, _ = resolve_unit_id(experiment, user_id, session_id)
                assignment = assign_variant(experiment, unit_id, store)
                agent_id = assignment.agent_id
                metadata |= {
                    "ab_experiment": assignment.experiment,
                    "ab_variant": assignment.variant,
                    "ab_variant_version": assignment.version,
                }

        try:
            agent = get_agent(agent_id)
        except KeyError as exc:
            return JSONResponse({"error": f"omni agent misconfigured: {exc}"}, status_code=500)

        result = await agent.arun(
            input=text,
            user_id=user_id,
            session_id=session_id,
            stream=False,
            metadata=metadata,
        )
        reply = getattr(result, "content", None)
        return JSONResponse({"reply": reply if isinstance(reply, str) else str(reply)})
