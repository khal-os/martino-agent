# AGENTS.md — nsmtx-agent-template

Instructions for developers and AI coding agents working on this repo.

## What this is
A production-shaped template for building Agno agents (AgentOS + tools + hooks +
prompts + evals + Docker). Copy it to start a new agent service.

## Repository structure
```
src/agent_app/
├── agents/            # ONE MODULE PER AGENT + registry (BUILDERS) — extend here
├── prompts/<agent>/   # system prompts as versioned .md files (git = history)
├── tools/             # deterministic @tool functions
├── hooks/             # pre_hooks (input gates) · post_hooks (output guards) · tool_hooks
├── experiments/       # A/B testing: sticky routing + remote % control + monitor (docs/ab-testing.md)
├── omni.py            # Automagik Omni webhook adapter (POST /omni/webhook) — docs/omni.md
├── config.py          # frozen Settings, fail-fast env
├── models.py          # LLM provider factory (+ Anthropic prompt caching)
├── context.py         # volatile context as message-0 (cache-safe)
├── db.py · knowledge.py · observability.py · prompt_loader.py
└── main.py            # AgentOS → FastAPI + Bearer auth + DB-aware /health
evals/                 # behavioral contract: python -m evals (real model)
tests/                 # offline unit/wiring tests (no model calls)
docs/                  # architecture · adding-an-agent · prompt-caching · observability · agno-features
```

## Core rules
1. **LLM decides *what*; code decides *how*.** Business logic, API auth, retries,
   PII handling live in Python tools — never delegated to the model.
2. **Prompts are files** under `prompts/<agent>/system.md`, reviewed like code.
   Static only — volatile data goes through `context.py` (message-0), or the
   prompt cache is busted (docs/prompt-caching.md).
3. **Agents are built once** via the registry (`agents/BUILDERS`) — never
   instantiate agents inside loops or request handlers.
4. **Every new agent ships with**: prompt file, registry entry, ≥1 offline test,
   ≥1 eval case (docs/adding-an-agent.md).

## Commands
```bash
make install   # uv venv + deps
make check     # lint (ruff) + types (mypy strict) + offline tests  ← run before pushing
make dev       # local server :8888 (SQLite fallback, /docs)
make eval      # eval suite vs real model (costs tokens — not in the unit loop)
make up        # docker: app + postgres
make langwatch-up  # self-hosted LangWatch (observability)
```

## Testing policy
- `tests/` = offline, deterministic, CI-blocking. No network, no model.
- `evals/` = real-model behavioral checks (ReliabilityEval + AgentAsJudgeEval),
  run nightly/pre-release via `make eval`. Add a `Case` per behavior you rely on.
