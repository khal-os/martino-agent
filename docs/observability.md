# Observability via the connector register

The agent is **vendor-neutral**: it knows nothing about LangWatch (or any other
observability platform). It emits standard OTLP-over-HTTP traces and HTTP+JSON
events to endpoints it resolves at runtime from a per-client **connector
register**. The connector owns the contracts; the agent just plugs in.

## 1. The one and only setting

```bash
CONNECTOR_REGISTER_URL=https://connectorregister.<client>.example.com
```

Unset → tracing off (the app runs fine without it). Everything else — where
traces/events go, with which credentials, for how long the answer is valid — is
resolved from the register at runtime, so the platform can move hosts, rotate
keys or swap vendors **without touching any agent config or restarting it**.

Never put a vendor endpoint or API key in the agent's env.

## 2. The register contract (v1)

`GET {CONNECTOR_REGISTER_URL}` — the env URL is the complete entry point,
fetched verbatim; the agent never constructs URLs — returns a hypermedia
document:

```json
{
  "version": "1",
  "ttl_seconds": 300,
  "links": {
    "traces": {"href": "https://…/api/otel/v1/traces", "method": "POST",
               "headers": {"Authorization": "Bearer sk-…"}},
    "events": {"href": "https://…/api/track_event", "method": "POST",
               "headers": {"X-Auth-Token": "sk-…"}}
  }
}
```

Client obligations (implemented by `ConnectorClient` in
[connector.py](../src/agent_app/connector.py)):

- **cache** the document for `ttl_seconds` (register-declared; default 300);
- **re-fetch** on expiry *and* on link failure (`invalidate()`), so key
  rotations and host moves propagate within the TTL;
- treat `headers` as **opaque** — copied verbatim onto every request (this is
  where auth lives; the agent never inspects it);
- **ignore unknown links**; an absent link disables that capability;
- **best-effort always**: a register outage never crashes or blocks the agent —
  the batch/event is dropped, stale links keep serving, retries are throttled
  (15 s).

How the links are consumed ([observability.py](../src/agent_app/observability.py)):

- `traces` → `_ConnectorSpanExporter` re-resolves the link on each batch export
  (cached, so it's cheap) and lazily rebuilds the inner `OTLPSpanExporter`
  whenever `href`/`headers` change. Export failure → `invalidate()` → next
  batch re-resolves immediately.
- `events` → `track_event()` POSTs the payload to `link.href` with the
  register's headers plus `Content-Type: application/json`.

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
link. `POST /feedback` ([routes.py](../src/agent_app/routes.py)) is the wired
example — point your UI's thumbs at it; get the `trace_id` from the run (or
`current_trace_id()`). Absent `events` link → capability off, returns `False`.

## 7. Dev / local: LangWatch stack + a local register

The template still ships a throwaway LangWatch instance for development:

```bash
make langwatch-up      # full stack: app(:5560)+workers+nlp+langevals+postgres+redis+clickhouse
make langwatch-init    # DEV-ONLY: auto-creates account+org+project, writes LANGWATCH_API_KEY to .env
```

`langwatch-init` (`scripts/langwatch_bootstrap.sh`) is idempotent and refuses
to run when `ENVIRONMENT` is staging/prod. **The agent does not read
`LANGWATCH_API_KEY`** — the key's job is to go into the *register document*.
There's no register service in dev, so serve the document as a static file
(this is the "mock connector"):

```bash
mkdir -p tmp/register && cat > tmp/register/index.json <<EOF
{
  "version": "1",
  "ttl_seconds": 60,
  "links": {
    "traces": {"href": "http://localhost:5560/api/otel/v1/traces",
               "headers": {"Authorization": "Bearer $LANGWATCH_API_KEY"}},
    "events": {"href": "http://localhost:5560/api/track_event",
               "headers": {"X-Auth-Token": "$LANGWATCH_API_KEY"}}
  }
}
EOF
python -m http.server 8765 -d tmp/register
```

Then run the agent with:

```bash
CONNECTOR_REGISTER_URL=http://localhost:8765/index.json make dev
```

Every run is now traced into the local LangWatch UI at http://localhost:5560.
Edit the JSON (new key, new host) and the agent picks it up within
`ttl_seconds` — no restart. In staging/prod you don't do any of this: the
platform team runs the real register; the agent gets only
`CONNECTOR_REGISTER_URL` from the secrets manager.

## Notes

- Telemetry is **fail-open** end to end: register down, link missing, deps not
  installed — the agent logs a warning and keeps serving.
- Install the extras once: `uv pip install -e ".[observability]"`
  (opentelemetry-sdk + OTLP-HTTP exporter + `openinference-instrumentation-agno`).
- The `langwatch` SDK itself is only a **qa extra** now, used by the offline
  evals ([evals/](../evals/)) which read `LANGWATCH_*` from their own env — the
  serving path never imports it.
- Offline tests mock the register by monkeypatching `urllib.request.urlopen`
  ([tests/test_observability.py](../tests/test_observability.py)) — same code
  path, canned document, no network.

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
