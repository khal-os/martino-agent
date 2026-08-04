# Observability via the Connector Catalog

The agent is **vendor-neutral**: it knows nothing about LangWatch (or any other
observability platform). It emits standard OTLP-over-HTTP traces to endpoints
it resolves at runtime from the khal **Connector Catalog**. The connector owns
the contracts; the agent just plugs in.

## 1. The two settings

```bash
CONNECTOR_CATALOG_URL=https://connectorcatalog.<client>.example.com
M2M_TOKEN=<the agent's M2M token>   # issued by the Agent Catalog (see §7)
```

(`CONNECTOR_REGISTER_URL`, the pre-Catalog-rename spelling, still works.)

Either unset → tracing off (the app runs fine without it; url without token
logs one warning at boot). Everything else — where traces go, with which
credentials, for how long the answer is valid — is resolved from the catalog
at runtime, so the platform can move hosts, rotate keys or swap connectors
**without touching any agent config or restarting it**.

Never put a vendor endpoint or API key in the agent's env. `M2M_TOKEN` is the
agent's **identity** token toward all khal services (catalogs and modules) —
not a Connector-Catalog-specific secret. Its claims are
`{tenant, client_id, client_secret}`. Auth is identity-only: a valid token in
the right tenant is enough — there are **no scopes** in the M2M model.

## 2. The catalog contract (capability resolution)

`POST {CONNECTOR_CATALOG_URL}/connections` with an **intent** — the agent
never asks for a connector by id; it states *what* it needs and *how* it can
speak (the full usage-intent tuple: signal, operation, transport, protocol,
protocol version, encoding):

```json
{
  "capability": {"signal": "monitoring.trace", "operation": "write"},
  "binding": {"transport": "http", "protocol": "otlp",
              "protocolVersion": "1.0", "encoding": "protobuf"}
}
```

→ a resolved connection:

```json
{
  "connectorId": "langwatch-cliente",
  "connectsTo": "monitoring",
  "resolvedUrl": "https://…/api/otel/v1/traces",
  "ttlSeconds": 900,
  "chosenBinding": {"transport": "http", "protocol": "otlp", "encoding": "protobuf", "...": "..."},
  "credential": {"placement": "header", "name": "authorization",
                 "scheme": "Bearer", "value": "sk-…"}
}
```

Link names map to intents in `_INTENTS` ([connector.py](../src/agent_app/connector.py)):
`traces` → `monitoring.trace`/`write` over http/otlp/1.0/protobuf. `events` has
**no signal in the platform vocabulary yet** — the capability is off (resolves
to None without any HTTP call) until one exists.

Client obligations (implemented by `ConnectorClient`):

- **cache** each resolution for its `ttlSeconds` (credential freshness;
  default 300 when absent) — the catalog is resolve-once, never a proxy;
  there is no session renew: expired means resolve again;
- **re-resolve** on expiry *and* on link failure (`invalidate()`), so key
  rotations and connector moves propagate within the TTL;
- **apply the credential where the response says**: `header` placement →
  request header (`Bearer`/`Basic` prefix the scheme; `ApiKey` sends the bare
  value — vendor headers like `X-Api-Key` take the raw key); `query` placement
  → parameter appended to the resolved URL;
- `404 no_connector_for_capability` → capability **off**, negative-cached so
  the catalog isn't hammered; re-checked after the TTL;
- **best-effort always**: a catalog outage or auth error never crashes or
  blocks the agent — the batch is dropped, stale links keep serving, retries
  are throttled (15 s); problem+json `code`s are logged.

How the links are consumed ([observability.py](../src/agent_app/observability.py)):

- `traces` → `_ConnectorSpanExporter` re-resolves the link on each batch export
  (cached, so it's cheap) and lazily rebuilds the inner `OTLPSpanExporter`
  whenever `href`/`headers` change. Export failure → `invalidate()` → next
  batch re-resolves immediately.
- `events` → `track_event()` returns False while the capability is off (no
  event signal on the platform yet).

## 3. What gets traced automatically

`setup_observability()` installs a plain OTel `TracerProvider` and the Agno
OpenInference instrumentor **before** the agent is built. Every run then emits
a span tree: the agent run → each LLM call (with `llm.token_count.*`, incl.
cache read/write — what the platform prices traces from) → each tool call. No
per-call code needed.

The agent's **payload is frozen** — standard OTel semconv plus whatever the
instrumentor naturally produces. Adapting it to any platform's conventions
(module mapping, key renames) happens on the connector side, never in the
agent.

## 4. Custom spans (extra instrumentation points)

Use a plain OTel tracer to give a sub-step (external API call, DB query,
retrieval) its own node in the trace tree — see
[example_tools.py](../src/agent_app/tools/example_tools.py)`::lookup_price`:

```python
from opentelemetry import trace

tracer = trace.get_tracer("agent_app.tools")
with tracer.start_as_current_span("price-catalog-lookup") as span:
    span.set_attribute("openinference.span.kind", "TOOL")  # TOOL | LLM | RETRIEVER | ...
    span.set_attribute("input.value", item)
    price = catalog.get(item)
    span.set_attribute("output.value", price)
```

`openinference.span.kind` controls how the node renders; `input.value` /
`output.value` surface as the node's payloads.

## 5. Rich trace metadata (two levels)

**Static / resource (once per process)** — `_resource_attributes()` stamps the
OTel Resource on every span:

```python
{ "service.name": settings.service_name,          # OTEL_SERVICE_NAME / AGENT_ID
  "service.version": settings.agent_version,       # single source: _version.py
  "deployment.environment": settings.environment,  # ENVIRONMENT (dev|staging|prod)
  "agent.version": settings.agent_version,         # additive: passed through verbatim
  "agent.instance": settings.agent_instance,       # AGENT_INSTANCE (default: hostname)
  "vcs.revision": settings.git_sha,                # GIT_SHA — only when != "unknown"
  "model.provider": ..., "model.id": ...,
  "domain": settings.domain,                       # DOMAIN — only when set
  "subdomain": settings.subdomain }                # SUBDOMAIN — only when set
```

(`agent.version`/`agent.instance` exist because some platforms drop the semconv
spellings at ingestion; the custom keys pass through verbatim. `domain`/
`subdomain` are the observability platform's trace-filter keys — static per
deployment, so they ride the Resource.)

**Dynamic / per-request** — the `enrich_trace` pre-hook
([pre_hooks.py](../src/agent_app/hooks/pre_hooks.py)) calls
`enrich_current_trace(...)` on every turn:

- `user_id` / `session_id` / `customer_id` → reserved keys the platform
  promotes to trace-level fields (group/filter by user, conversation, tenant);
- `channel` / `channel_version` / `channel_instance` → BARE
  `channel`/`channel.version`/`channel.instance` span attributes — the
  platform's channel contract keys. Resolution order: omni run metadata (the
  real channel: whatsapp/discord/…) → the request's `X-Channel-Type` /
  `X-Channel-Version` / `X-Channel-Instance` headers (UIs and integrations
  declare themselves — e.g. a browser UI sends `X-Channel-Type: browser`;
  captured by `ChannelHeaderMiddleware`, middleware.py) → the `CHANNEL` env
  default (`api`);
- everything in `metadata={...}` → `app.<key>` span attributes (tenant, plan,
  turn, …) — backend-agnostic, always queryable.

**Key-naming rule:** fields that are part of the platform module's metadata
contract (`channel`, `domain`, `subdomain`, `agent.*`, `ab.*`) use bare keys;
free-form app context goes under the `app.` prefix.

A/B arms are stamped the same way: `tag_experiment()` sets `ab.experiment` /
`ab.variant` / `ab.variant_version` — see [ab-testing.md](ab-testing.md).

## 6. Product events (👍/👎, ratings, conversions)

`track_event(trace_id, event_type, metrics=…, details=…)` attaches real
outcomes to the trace that produced them, through the register's `events`
capability. `POST /feedback` ([routes.py](../src/agent_app/routes.py)) is the
wired example — point your UI's thumbs at it; get the `trace_id` from the run
(or `current_trace_id()`). The capability is **off** until the platform
vocabulary gains an event signal → `track_event` returns `False`.

## 7. Dev / local: LangWatch stack + the real Connector Catalog

Dev runs the **real** khal Connector Catalog — there is no mock. Three
pieces:

**a) LangWatch** (the trace store):

```bash
make langwatch-up      # full stack: app(:5560)+workers+nlp+langevals+postgres+redis+clickhouse
make langwatch-init    # DEV-ONLY: auto-creates account+org+project, writes LANGWATCH_API_KEY to .env
```

**The agent does not read `LANGWATCH_API_KEY`** — the key's job is to be
seeded into the catalog's dev vault.

**b) The Connector Catalog** (from the khal-platform monorepo — the service is
still named `connector-register` there until the platform lands the rename),
with the key seeded so resolutions carry the *real* credential (locally the
vault is an in-memory fake — see khal-platform
`docs/platform/connector-register/sops.md`):

```bash
cd <khal-platform>
VAULT_CREDENTIALS_JSON='{"workos-vault://langwatch-cliente":"<LANGWATCH_API_KEY>"}' \
  pnpm --filter @khal/connector-register dev        # :7103 (NOT via turbo — strict env)
```

Then register the connector (in-memory — repeat after every catalog restart;
the script lives with the connector, in observability-module):

```bash
OTLP_ENDPOINT=http://localhost:5560/api/otel/v1/traces \
  ../observability-module/scripts/connector/register.sh
```

**c) The agent**, pointed at the catalog with its M2M token. The token is
issued by the **Agent Catalog** when the agent is registered
(`scripts/khal_register_agent.sh`, then the token route):

```bash
export CONNECTOR_CATALOG_URL=http://127.0.0.1:7103
export M2M_TOKEN=$(curl -s -X POST http://127.0.0.1:7104/agents/martino/token \
  -H 'content-type: application/json' \
  -d '{"tenant":"acme","client_secret":"<secret from the agent PUT>"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
make dev
```

> **LEGACY — works today, delete once the platform ships the token route and
> drops scope checks.** The target model has NO scopes (identity-only auth),
> but the local catalog still enforces them and has no token route yet, so
> mint a dev claims token (base64url JSON, read verbatim) carrying the legacy
> scopes:
>
> ```bash
> export M2M_TOKEN=$(python3 -c "import base64,json;print(base64.urlsafe_b64encode(json.dumps({'tenant':'acme','client_id':'martino','scope':'connectors.connection:resolve monitoring.trace:write'}).encode()).decode().rstrip('='))")
> ```

Every run is now traced into the local LangWatch. Rotate the key or move the
connector (re-register with a new endpoint) and the agent picks it up within
`ttlSeconds` — no restart. In staging/prod you don't do any of this: the
platform team runs the catalog and the vault is real (WorkOS Vault); the
agent gets `CONNECTOR_CATALOG_URL` + `M2M_TOKEN` from the
secrets manager.

## Notes

- Telemetry is **fail-open** end to end: catalog down, link missing, deps not
  installed — the agent logs a warning and keeps serving.
- Install the extras once: `uv pip install -e ".[observability]"`
  (opentelemetry-sdk + OTLP-HTTP exporter + `openinference-instrumentation-agno`).
- The `langwatch` SDK itself is only a **qa extra** now, used by the offline
  evals ([evals/](../evals/)) which read `LANGWATCH_*` from their own env — the
  serving path never imports it.
- Offline tests fake the catalog by monkeypatching `urllib.request.urlopen`
  ([tests/test_observability.py](../tests/test_observability.py)) — same code
  path, canned resolution documents, no network.

## Learn more

- **Connector contract & client semantics** — module docstring in
  [connector.py](../src/agent_app/connector.py) (the source of truth)
- **OpenTelemetry OTLP/HTTP exporter** —
  https://opentelemetry.io/docs/languages/python/exporters/
- **OpenInference semantic conventions** (span kinds, `input.value`/`output.value`) —
  https://github.com/Arize-ai/openinference/tree/main/spec
- **LangWatch OpenTelemetry ingestion** (what the dev stack accepts) —
  https://langwatch.ai/docs/integration/opentelemetry/guide
- **LangWatch custom user events** (`/api/track_event`) —
  https://langwatch.ai/docs/user-events/custom
- Full feature map (what's wired vs pluggable): [langwatch-features.md](langwatch-features.md)
