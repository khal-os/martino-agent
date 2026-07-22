# Omnichannel with Automagik Omni (WhatsApp / Slack / Discord / …)

[Automagik Omni](https://github.com/automagik-dev/omni) is an omnichannel messaging
hub: it connects one API to WhatsApp, Slack, Discord, Telegram and more, and
dispatches inbound messages to an **agent backend** registered as a *webhook
provider*. This template ships the agent side of that integration — `POST
/omni/webhook` — plus an **unofficial local docker stack** to spin Omni up for
development.

```
WhatsApp/Slack/… ──▶ Omni ──(webhook POST)──▶ POST /omni/webhook ──▶ agent.run ──▶ {"reply"} ──▶ Omni ──▶ channel
```

Two integration modes, both documented below:
- **baseline** — every message runs one agent (`OMNI_AGENT_ID`).
- **A/B** — set `OMNI_EXPERIMENT` and channel messages route through an experiment
  (see [ab-testing.md](ab-testing.md)), sticky per sender. Real omnichannel A/B:
  each WhatsApp number consistently gets the same arm, every turn tagged
  `ab.variant` in LangWatch.

> **Repo note.** Omni v2 (`automagik-dev/omni`, TypeScript/Bun, event-driven) is
> the active project. The older Python `namastexlabs/automagik-omni` is archived.
> This doc targets v2.

---

## The webhook contract (what our adapter implements)

When a message arrives, Omni's webhook provider (round-trip mode) POSTs to our
`base_url` with `Authorization: Bearer <provider apiKey>`, `X-Omni-Provider: webhook`
and this body (fields we use in **bold**):

```jsonc
{
  "event":   { "id": "...", "type": "message", "timestamp": 0 },
  "instance":{ "id": "...", "channelType": "whatsapp" },   // → trace metadata
  "chat":    { "id": "..." },                              // → session_id
  "sender":  { "id": "...", "name": "...", "personId": "..." }, // sender.id → user_id
  "content": { "text": "how much is milk?", "emoji": null },   // → the agent input
  "traceId": "...",
  "replyEndpoint": "POST /api/v2/messages/send"
}
```

Our `/omni/webhook` (see `src/agent_app/omni.py`) maps `sender.id → user_id`,
`chat.id → session_id`, runs the agent, and returns what round-trip mode expects:

```json
{ "reply": "milk: R$4.50" }
```

Empty/reaction/media-only events return `{"reply": ""}` (no-op). Unknown payload
fields are ignored, so schema drift on Omni's side won't break us.

**Auth.** The route is behind the app's Bearer gate (`middleware.py`). Register the
Omni provider with `apiKey` = this app's `API_KEY`, and Omni's `Authorization:
Bearer` header passes the gate. In dev with `API_KEY` unset, the route is open.

---

## Configure the template side

`.env`:
```bash
# Which agent answers channel messages (baseline). Defaults to AGENT_ID.
OMNI_AGENT_ID=assistant
# Route channel messages through an A/B experiment instead (sticky per sender).
# Empty = baseline (single agent).
OMNI_EXPERIMENT=assistant-tone
```
That's it — `POST /omni/webhook` is always mounted (see `main.py`).

### Try it without Omni (simulate a channel message)
```bash
curl -X POST localhost:8888/omni/webhook \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"content":{"text":"how much is milk?"},
       "sender":{"id":"5511999","name":"Alice"},
       "chat":{"id":"chat-1"},"instance":{"channelType":"whatsapp"}}'
# → {"reply":"..."}   (baseline, or an A/B arm if OMNI_EXPERIMENT is set)
```

---

## Local Omni stack (unofficial, for validation)

A `docker-compose.omni.yml` brings Omni up on your machine, **reusing the agno
Postgres** (a separate `omni` database) so you don't run a second DB. It adds
NATS/JetStream and stores media on local disk (`OMNI_MEDIA_MODE=local` — no
S3/MinIO).

```bash
make up            # agno stack (Postgres on the shared network) — required first
make omni-up       # Omni API → http://localhost:8882 (Swagger /api/v2/docs)
make omni-logs     # tail
make omni-down     # stop (the `omni` database persists in agno Postgres)
```
`OMNI_API_KEY` seeds a known bearer key on first boot (dev default
`omni_sk_dev_local`) so you can call the Omni API without scraping the startup
banner.

**Verified end-to-end:** Omni boots `healthy` (Postgres reachable — migrations run
on boot against the reused `omni` DB — NATS connected, plugins loaded), the seeded
API key authenticates, and registering the webhook provider (below) succeeds.
```bash
curl -s localhost:8882/health
# {"status":"healthy","checks":{"database":{"status":"ok"},"nats":{"status":"ok"},...}}
```

> ⚠️ **Port collision.** Omni's convention is `8882`. If you **already run Omni
> natively** (e.g. via PM2/`omni serve`), it holds host `:8882` and *shadows* the
> container — your `curl localhost:8882` then hits the native Omni, not this stack
> (symptom: `401` for the seeded key, since the native install has its own keys).
> Set `OMNI_HOST_PORT` to a free port to avoid it:
> ```bash
> OMNI_HOST_PORT=8899 make omni-up   # → http://localhost:8899
> ```

## Wire Omni → this agent

Register the agent as a webhook provider (`$OMNI` = Omni base URL, `$OMNI_KEY` =
the seeded/your Omni API key, `$AGENT_URL` = where Omni reaches this agent —
`http://app:8888/omni/webhook` when both are on the `agent-net` network):

```bash
curl -X POST $OMNI/api/v2/providers \
  -H "Authorization: Bearer $OMNI_KEY" -H "Content-Type: application/json" \
  -d '{"name":"agent-template","schema":"webhook",
       "baseUrl":"'"$AGENT_URL"'","apiKey":"'"$API_KEY"'",
       "schemaConfig":{"mode":"round-trip"}}'
```
(`apiKey` is what Omni sends as `Authorization: Bearer` to our webhook — set it to
this app's `API_KEY`.) Then create/point an instance at this provider and connect
a channel per Omni's docs. Inbound messages now flow to `/omni/webhook` — baseline
or, with `OMNI_EXPERIMENT` set, through your A/B experiment.

## Omni's supported self-host (Helm/k3d)

The compose above is a local convenience. Omni v2's **supported** OSS self-host
path is Kubernetes via the bundled Helm chart (autopg Postgres + NATS + MinIO).
From a clone of `automagik-dev/omni`: `make -C deploy deploy` into a local
k3d/OrbStack/kind cluster (see the repo's `deploy/README.md`). Use that for a
production-shaped deployment.

---

## References
- Automagik Omni v2: <https://github.com/automagik-dev/omni>
- Omni deploy (Helm/k3d): `deploy/README.md` in that repo
- Template adapter: `src/agent_app/omni.py` · A/B routing: [ab-testing.md](ab-testing.md)
