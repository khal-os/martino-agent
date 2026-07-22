# Observability with LangWatch

[LangWatch](https://langwatch.ai) is an OpenTelemetry-based LLM observability
platform (traces, cost/token analytics, evals). It's self-hostable, and this
template ships both the stack and the agent wiring.

## 1. Point the agent at LangWatch

### Dev / local (spin up a throwaway instance)
```bash
make langwatch-up      # full stack: app(:5560)+workers+nlp+langevals+postgres+redis+clickhouse
make langwatch-init    # DEV-ONLY: auto-creates account+org+project, writes LANGWATCH_API_KEY to .env
make dev               # every run is now traced
```
`langwatch-init` (`scripts/langwatch_bootstrap.sh`) automates the from-scratch
account/project setup so you don't click through the UI. It's idempotent and
**dev-only** — it talks better-auth + reads the DB directly, and refuses to run
when `ENVIRONMENT` is staging/prod. The stack is vendored from LangWatch's
official `compose.yml`; its secrets live in `langwatch/.env`.

### Staging / prod (use a managed instance)
You do **not** bootstrap or self-host per-deploy. Point at LangWatch Cloud or a
shared, already-provisioned self-hosted instance by injecting from your secrets
manager — no script:
```bash
LANGWATCH_ENABLED=1
LANGWATCH_ENDPOINT=https://app.langwatch.ai      # or your shared instance
LANGWATCH_API_KEY=${from_secrets_manager}
```
Then `make dev` (local) or `make up` (Docker). That's it.

## 2. What gets traced automatically
`observability.py` calls `langwatch.setup(..., instrumentors=[AgnoInstrumentor()])`
**before** the agent is built. The Agno OpenInference instrumentor then emits a
span tree for **every run**: the agent run → each LLM call (with tokens/cost) →
each tool call. No per-call code needed.

```python
# src/agent_app/observability.py (essence)
import langwatch
from openinference.instrumentation.agno import AgnoInstrumentor

langwatch.setup(
    api_key=settings.langwatch_api_key,
    endpoint_url=settings.langwatch_endpoint,
    instrumentors=[AgnoInstrumentor()],
)
```

## 3. Custom spans (extra instrumentation points)
Wrap any sub-step you want to see as its own node in the trace — an external API
call, a DB query, a retrieval step. Use the context manager (inline) or the
decorator (whole function).

**Context manager** — see `tools/example_tools.py::lookup_price`:
```python
import langwatch

with langwatch.span(type="tool", name="price-catalog-lookup") as span:
    span.set_input({"item": item})
    price = catalog.get(item)
    span.set_output({"price": price})
```

**Decorator** — trace a whole function:
```python
@langwatch.span(type="rag", name="retrieve-docs")
def retrieve(query: str):
    ...
```
`type` is one of `llm | rag | tool | agent | span | ...` and controls how the
node renders. Set `span.update(contexts=[RAGChunk(...)])` on RAG spans to capture
retrieved documents.

## 4. Rich trace metadata (two levels)
Good metadata makes traces *filterable*. The template sets it at two levels:

**Static / resource (once per process)** — `setup_observability` passes
`base_attributes` to `langwatch.setup()`, which becomes the OTel **Resource**
stamped on every span. This is what turns `service.name: unknown_service` into
your service, and adds version/env/model:
```python
{ "service.name": settings.service_name,         # OTEL_SERVICE_NAME / AGENT_ID
  "service.version": settings.agent_version,      # single source: _version.py
  "deployment.environment": settings.environment, # ENVIRONMENT (dev|staging|prod)
  "vcs.revision": settings.git_sha,               # GIT_SHA — only added when != "unknown"
  "model.provider": ..., "model.id": ... }
```

**Dynamic / per-request** — the `enrich_trace` pre-hook calls
`enrich_current_trace(...)`, which sets attributes on the active span:
- reserved LangWatch keys for grouping — `langwatch.user.id`, `langwatch.thread.id`
  (conversation), `langwatch.customer.id` (tenant);
- everything in `metadata={...}` → `app.<key>` attributes (tenant, channel,
  plan, turn, …) — backend-agnostic and always queryable.

> Why span attributes and not `langwatch.get_current_trace().update()`? Under the
> OpenInference instrumentor there's no LangWatch-native trace in context
> (`get_current_trace()` returns None), so setting attributes on the current OTel
> span is the reliable path. Agno also sets user/thread from the run's
> `user_id`/`session_id` automatically.

> ⚠️ The LangWatch **trace-list** view shows a *curated subset* of metadata
> (service.name, model, user, thread). The full set (service.version,
> deployment.environment, `app.*`) is on each span and visible in the **trace
> detail** view / queryable in ClickHouse — it's all stored, just not all listed.

## Notes
- Telemetry is **fail-open**: if LangWatch is down or deps are missing, the agent
  logs a warning and keeps serving (`observability.py`).
- Install the extras once: `uv pip install -e ".[observability]"`
  (adds `langwatch` + `openinference-instrumentation-agno`).
- LangWatch speaks OpenTelemetry, so the same spans can also be fanned out to
  Langfuse/Grafana/etc. if you add another OTLP exporter — but one platform is
  plenty to start.

## Learn more
- **LangWatch — Python integration** (`langwatch.setup`, spans) —
  https://langwatch.ai/docs/integration/python/guide
- **OpenTelemetry integration** (how instrumentors/exporters wire up) —
  https://langwatch.ai/docs/integration/opentelemetry/guide
- **Custom user events** (`/api/track_event` — 👍/👎, ratings) —
  https://langwatch.ai/docs/user-events/custom
- **Agno tracing** (`AgentOS(tracing=True)`) — https://docs.agno.com/agent-os/introduction
- Full feature map (what's wired vs pluggable): [langwatch-features.md](langwatch-features.md)
