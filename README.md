# Namastex Agent Template

A **production-shaped template** for building agents on the [Agno](https://docs.agno.com)
framework. Copy it, rename `agent_app`, and you have a running agent with tools,
hooks, a knowledge base, session state, a REST API (AgentOS), and Docker deploy —
following the patterns used by Namastex's live agents (eugenia, renan).

## What you get
- **Multi-agent by design** — one module per agent + a registry (`agents/BUILDERS`);
  adding an agent is a 5-file checklist. → [`docs/adding-an-agent.md`](docs/adding-an-agent.md)
- **Prompt management** — prompts are versioned markdown files (`prompts/<agent>/system.md`)
  with per-model variants, loaded by `prompt_loader.py`. Reviewed like code.
- **Evals** — `evals/` behavioral suite (`make eval`): ReliabilityEval (did the right
  tools fire?) + AgentAsJudgeEval (LLM judge vs rubric), exit-code CI-friendly.
- **Tool hooks** (logging/allowlist) + **pre/post hooks** (input/output guardrails).
- **Model fallback** — primary errors/rate-limits/overflows → run retries on a second
  (ideally cross-provider) model. `FALLBACK_PROVIDER`/`FALLBACK_MODEL_ID` in `.env`.
  Note: agno only falls back on 5xx/network/429/context-overflow — never on 4xx caller bugs.
- **A/B testing (multi-variant experiments)** — route live traffic between agent
  variants (A/B/C/D/E) with deterministic **sticky** bucketing, change the split
  **remotely without a redeploy**, and slice every arm in LangWatch (`ab.variant`).
  Ships a working `assistant-tone` example. → [`docs/ab-testing.md`](docs/ab-testing.md)
- **Omnichannel (Automagik Omni)** — `POST /omni/webhook` connects the agent to
  WhatsApp/Slack/Discord/… via Omni's webhook provider, baseline or routed through
  an A/B experiment (sticky per sender). Unofficial local stack: `make omni-up`.
  → [`docs/omni.md`](docs/omni.md)
- **Simple knowledge base** (PgVector RAG, seedable from markdown) — optional.
- **Session state** persisted to Postgres (SQLite fallback for zero-infra dev).
- **AgentOS** REST API (50+ endpoints) with Bearer auth and a DB-aware `/health`.
- **Prompt caching** (Anthropic) done right — static system prompt cached, volatile
  context (date, user data) injected as "message 0". ~10× input-cost cut. → [`docs/prompt-caching.md`](docs/prompt-caching.md)
- **Observability**: self-hosted **LangWatch** stack + auto-tracing of every run +
  custom-span examples. → [`docs/observability.md`](docs/observability.md)
- **Structured logging** — **structlog** (JSON in prod, pretty console in dev). Stdlib
  logging is routed through it, every line carries the run's OTel `trace_id` + bound
  `user_id`/`session_id`, so logs join to LangWatch traces. `LOG_LEVEL`/`LOG_JSON` in `.env`.
- **Strict quality gate** — **Ruff** (turned-up rule set: bugbear, security, pyupgrade,
  isort, …) + **mypy `--strict`** on the shipped package, all wired into `make check`.
- **Docker + docker-compose** (app + Postgres/pgvector) and a **PM2** file — ready to ship.

## Quickstart (local, no Docker)
```bash
cp .env.example .env          # set MODEL_PROVIDER + a model key
make install                  # uv venv + deps
make test                     # offline wiring tests (no model calls)
make dev                      # http://localhost:8888  (docs at /docs)
```
With no `DATABASE_URL`, it uses a local SQLite file — boots with just a model key.

## Quickstart (Docker — app + Postgres)
```bash
cp .env.example .env          # set your model key(s)
make up                       # builds image, starts app + pgvector Postgres
curl localhost:8888/health
make logs
```

## Talk to it
```bash
# AgentOS run endpoint takes multipart form fields (not JSON).
# Add `-H "Authorization: Bearer $API_KEY"` if API_KEY is set.
curl -X POST localhost:8888/agents/assistant/runs \
  -F "message=add milk to my cart, then show it" \
  -F "session_id=demo1" -F "stream=false"
```
Interactive API docs live at **`/docs`**.

## Enable knowledge (RAG)
```bash
# in .env: KNOWLEDGE_ENABLED=1, DATABASE_URL=..., OPENAI_API_KEY=<embedder key>
make seed                     # embeds knowledge_base/*.md into PgVector
```

## Enable observability (LangWatch)
```bash
make install-obs              # observability + QA extras (langwatch, scenario, pandas)
make langwatch-up             # self-hosted LangWatch stack → http://localhost:5560
make langwatch-init           # DEV-ONLY: auto-creates project + writes LANGWATCH_API_KEY to .env
```
The agent itself never reads `LANGWATCH_API_KEY` — tracing turns on via
`CONNECTOR_REGISTER_URL` + `CONNECTOR_REGISTER_TOKEN`, pointing at the khal
**connector-register**, which resolves the trace endpoint + credential by
capability (`POST /connections`). In dev you run the real register from the
khal-platform monorepo with the LangWatch key seeded into its dev vault — see
[`docs/observability.md`](docs/observability.md) §7 for the copy-paste steps:

```bash
make dev                      # with the register up: every run is traced (tokens, cost, tools, spans)
```
`make scenario` and `make experiment` (the LangWatch QA lanes) also need `make install-obs`.

## Layout & docs
- [`docs/agno-features.md`](docs/agno-features.md) — map of the Agno primitives (agent, state, tools, hooks, knowledge, memory, AgentOS).
- [`docs/architecture.md`](docs/architecture.md) — template structure, the Namastex house patterns, and what to add for production.
- [`docs/ab-testing.md`](docs/ab-testing.md) — multi-variant experiments: sticky routing, remote traffic control, and per-variant metrics in LangWatch.
- [`docs/deploy-aws.md`](docs/deploy-aws.md) — ship to AWS ECS Fargate (Aurora + ALB + SSM secrets) via Copilot: `make aws-up`.

## CI
`.github/workflows/ci.yml` runs the offline gate (`make check` — ruff + mypy
`--strict` + tests) on every push to `main` and every PR, across Python 3.11/3.12.
Real evals (`make eval`) cost tokens and stay out of CI — run them in a
nightly/pre-release lane.

## Where to customize
| Want to… | Edit |
|---|---|
| Change the persona/instructions | `src/agent_app/prompts/assistant/system.md` |
| **Add a new agent** | `src/agent_app/agents/` + registry — see [docs/adding-an-agent.md](docs/adding-an-agent.md) |
| Add a tool | `src/agent_app/tools/` |
| Add a guardrail | `src/agent_app/hooks/` |
| Add an eval case | `evals/cases.py` → `make eval` |
| **Add an A/B experiment / variant** | `src/agent_app/experiments/registry.py` — see [docs/ab-testing.md](docs/ab-testing.md) |
| Swap the LLM provider | `.env` (`MODEL_PROVIDER`) / `src/agent_app/models.py` |
| Add knowledge docs | `src/agent_app/knowledge_base/` + `make seed` |
| Deploy to a VM | `make prod-up` (hardened `docker-compose.prod.yml`) / `ecosystem.config.js` |
| Deploy to AWS (ECS Fargate) | `make aws-up` — see [docs/deploy-aws.md](docs/deploy-aws.md) |

## Model tiers (`@fast` / `@medium` / `@high`)
`MODEL_ID` (and `FALLBACK_MODEL_ID`) accept tier aliases resolved by the map in
`src/agent_app/models.py` (`MODEL_TIERS`, verified against the live provider APIs
on 2026-07-14 — re-check + bump when it ages):

| Provider | @fast | @medium | @high |
|---|---|---|---|
| anthropic | claude-haiku-4-5¹ | claude-sonnet-5 | claude-opus-4-8 |
| openai | gpt-5.6-luna | gpt-5.6-terra | gpt-5.6-sol |
| google | gemini-3.1-flash-lite | gemini-3.5-flash | gemini-3.1-pro-preview |

¹ Abbreviated — the pinned id is `claude-haiku-4-5-20251001` (see `MODEL_TIERS`).

Rule of thumb: `@fast` for wizards/classification/housekeeping, `@medium` as the
daily driver, `@high` for hard reasoning and judging.

## QA loop
```bash
make check                # lint (ruff) + types (mypy strict) + offline tests (free, CI gate)
make eval                 # single-turn behavioral evals vs the real model (agno judges)
make scenario             # multi-turn simulations (sim user + judge) → LangWatch Simulations
make experiment           # dataset batch eval + built-in evaluators → LangWatch Experiments
```
The four rungs (offline → single-turn → multi-turn → dataset) and the full
LangWatch feature map (what's wired vs pluggable) are in
[`docs/langwatch-features.md`](docs/langwatch-features.md).

## Version & health
One source of truth — `src/agent_app/_version.py` (also drives `pyproject`'s
version via hatch). It surfaces in `GET /health` (`version` + `git_sha` + `environment`)
and on every trace's `service.version`. Bump that one line to release.

Built on Agno 2.x. See <https://docs.agno.com/deploy/templates> for cloud starters.
