# Template Architecture & Namastex House Patterns

This template distills the patterns shared by the production Namastex agents
**genie-hv-eugenia** (Hapvida sales, WhatsApp) and **renan / Central de
Agendamentos** (Hapvida scheduling, WhatsApp) — both built on Agno + AgentOS.

## Layout

Aligned with the **official agno production template** (`agno-agi/agentos-docker-template`:
`agents/` one-module-per-agent, runnable `evals/`, extension docs) merged with the
Namastex house patterns (prompt files, hooks, caching, LangWatch):

```
nsmtx-agent-template/
├── src/agent_app/
│   ├── agents/          # ★ one module per agent + BUILDERS registry (extend here)
│   │   ├── __init__.py  #   registry: get_agents() / get_agent(id)
│   │   └── assistant.py #   the template agent (copy me)
│   ├── prompts/         # ★ prompt management: versioned .md per agent
│   │   └── assistant/system.md      (+ variants/<model-slug>/system.md)
│   ├── prompt_loader.py # loads/substitutes prompt files, per-model fallback
│   ├── config.py        # frozen Settings, fail-fast env (.env → repo root)
│   ├── models.py        # provider factory (+ Anthropic prompt caching)
│   ├── context.py       # volatile context as message-0 (cache-safe)
│   ├── db.py            # PostgresDb (prod) / SqliteDb (dev fallback)
│   ├── knowledge.py     # optional PgVector Knowledge (or filesystem-md variant)
│   ├── observability.py # LangWatch + AgnoInstrumentor (fail-open)
│   ├── tools/           # deterministic @tool functions (the agent's hands)
│   ├── hooks/           # pre_hooks, post_hooks, tool_hooks
│   ├── main.py          # AgentOS(agents=registry) + Bearer auth + /health
│   └── knowledge_base/  # markdown seeded into the vector store
├── evals/               # ★ behavioral contract: Case list + `python -m evals`
│   ├── cases.py         #   ReliabilityEval (tool calls) + AgentAsJudgeEval (rubric)
│   └── __main__.py      #   runner: exit 0/1, --case filter, -v
├── tests/               # offline unit/wiring tests (tools, hooks, prompts, smoke)
├── docs/                # this file · adding-an-agent · prompt-caching · observability
├── scripts/seed_knowledge.py
├── Dockerfile · docker-compose.yml · docker-compose.langwatch.yml
├── Makefile             # full loop: install/dev/check/eval/up/langwatch-up/deploy
├── AGENTS.md            # repo conventions for devs + AI coding agents
└── ecosystem.config.js  # PM2 for VM deploys
```

### Extensibility model
- **New agent** = prompt file + agent module + 1 registry line (+ test + eval case).
  Everything else (AgentOS routes, /health, evals) iterates the registry.
  See docs/adding-an-agent.md.
- **Prompt management** = files under `prompts/`, reviewed via PR, deployed via
  redeploy (which re-warms the prompt cache). Per-model variants supported.

### QA model (two lanes)
| Lane | What | When | Cost |
|---|---|---|---|
| `make test` | offline unit/wiring (tools, hooks, prompts, registry) | every commit, CI-blocking | free |
| `make eval` | real-model behavior: ReliabilityEval (right tools fired) + AgentAsJudgeEval (rubric pass/fail) | nightly / pre-release / prompt changes | tokens |

## The core design rule
**"The LLM decides *what*; code decides *how*."** Tools are deterministic
functions returning small structured results. Keep API auth, retries, ID
cascades, payload-building and PII handling in Python — never delegate them to
the model. This is the single most important pattern from both reference agents.

## Request lifecycle
```
HTTP → Bearer auth → AgentOS route → [pre_hooks] → LLM ⇄ [tool_hooks → tools]
     → [post_hooks] → response ; session_state + history persisted to db
```

## House patterns baked in
| Pattern | Where | From |
|---|---|---|
| Fail-fast typed `Settings`, `.env` at boot | `config.py` | both |
| Provider factory via env (proxy-friendly) | `models.py` | both (LiteLLM/OpenRouter) |
| Sessions in Postgres, SQLite dev fallback | `db.py` | both |
| Bearer-gated AgentOS, public `/health /docs` | `main.py` | both |
| DB-aware `/health` → 503 when down | `main.py` | renan |
| pre/post/tool hooks for guardrails | `hooks/` | both (urgency/handoff gates, output sanitize) |
| Single Agent + tools (no Team/Workflow) | `agents/assistant.py` | both |
| uv + non-root Docker + healthcheck | `Dockerfile` | both |
| PM2 with secrets in `.env` (not pm2 env) | `ecosystem.config.js` | both |

## Deliberately left out (add when you need them)
These exist in the production agents but are scope for a *starter*:
- **Dual DB** (sessions vs traces) and a **ResilientPostgresDb** wrapper for
  reconnect safety (agno-agi/agno#8196).
- **Observability extras**: the Langfuse fan-out and a dedicated `tracing.py` module (LangWatch itself IS wired — see observability.py).
- **Channel adapters**: Omni (WhatsApp/Telegram) via A2A `message:send`
  (non-streaming), agent-card rewrite, `SessionLockMiddleware` (serialize
  concurrent runs per session).
- **Handoff/human-transfer** ledger + barge-in gates (eugenia) — a Postgres
  state machine that mutes the bot when a human takes over.
- **Migrations** applied via a `/databases/all/migrate` endpoint or Makefile.
- **CX quality**: CSAT capture + LLM-as-judge scoring (renan).

## Knowledge: two options
1. **PgVector (this default)** — semantic hybrid search; needs an embedder key;
   seed with `make seed`. Best when you have many docs and want ranked recall.
2. **Filesystem markdown (eugenia/renan)** — ship a `knowledge/` folder and give
   the agent filesystem read/glob tools; the model greps it. No embeddings, no
   extra key, fully deterministic. Swap `knowledge.py` to build filesystem tools
   and append them to the toolset instead of passing `knowledge=`.

## Scaling
- **Horizontal**: stateless app; run N replicas behind a load balancer (sessions
  live in Postgres, not memory). Bump `--workers` per replica for concurrency.
- **DB**: managed Postgres (RDS/Cloud SQL/Neon) with pgvector. Split traces off.
- **Deploy targets**: `docker compose` (self-host), the Agno cloud starters
  (Railway/AWS/GCP/K8s — <https://docs.agno.com/deploy/templates>), or the
  house KHAL-FDE lane (`khal-app.json` + Gitea Actions) used by eugenia/renan.
