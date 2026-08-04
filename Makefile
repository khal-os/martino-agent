.DEFAULT_GOAL := help
.PHONY: help install install-obs dev run test test-cov lint format typecheck check \
        eval eval-case scenario experiment demo-fallback chat seed \
        net up down restart logs ps docker-build prod-up prod-down \
        langwatch-up langwatch-init langwatch-down stack-up stack-down \
        aws-up aws-redeploy aws-down aws-secrets aws-logs \
        clean deploy bump register-agent

# ─── Meta ────────────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ─── Setup & local dev ───────────────────────────────────────────────────────

install: ## Create venv and install deps incl. dev extras (uv)
	uv venv --allow-existing && uv pip install -e ".[dev]"

install-obs: ## Also install observability + QA extras (langwatch, scenario, pandas)
	uv pip install -e ".[observability,qa]"

dev: ## Run locally with hot reload (single process; SQLite unless DATABASE_URL set)
	uv run uvicorn agent_app.main:app --host 0.0.0.0 --port 8888 --reload

bump: ## Bump the agent version (PART=major|minor|patch, default patch)
	./scripts/bump_version.sh $(or $(PART),patch)

register-agent: ## Register this agent (+ its version) in the khal Agent Catalog
	./scripts/khal_register_agent.sh

run: ## Run locally like prod (workers, no reload)
	uv run uvicorn agent_app.main:app --host 0.0.0.0 --port 8888 --workers 2

# ─── Quality gates ───────────────────────────────────────────────────────────

test: ## Offline unit + wiring tests (no model calls, CI gate)
	uv run pytest -q

test-cov: ## Tests with coverage summary (fails under --cov-fail-under threshold)
	uv run pytest -q --tb=short -p no:cacheprovider \
		--cov=agent_app --cov-report=term-missing --cov-fail-under=70

lint: ## Lint (ruff check)
	uv run ruff check src tests scripts evals

format: ## Auto-format + fix imports (ruff)
	uv run ruff format src tests scripts evals && uv run ruff check --fix src tests scripts evals

typecheck: ## Static type check (mypy, strict)
	uv run mypy

check: lint typecheck test ## Full local gate: lint + types + tests (run before pushing)

# ─── Evals (real model — costs tokens; nightly/pre-release lane) ─────────────

eval: ## Run ALL eval cases against the real model (needs model key in .env)
	uv run python -m evals

eval-case: ## Run one eval case: make eval-case CASE=<substring>
	uv run python -m evals --case "$(CASE)" -v

scenario: ## Multi-turn simulation tests (sim user + judge) → LangWatch Simulations
	RUN_SCENARIOS=1 uv run pytest tests/scenarios -v -p no:cacheprovider

experiment: ## Batch-evaluate over a dataset → LangWatch Experiments
	uv run python evals/langwatch_experiment.py

demo-fallback: ## Live demo: primary model 503s → cross-provider fallback serves
	uv run python scripts/fallback_demo.py

chat: ## Minimal chat UI against the local agent (http://localhost:8899)
	uv run python scripts/chat_ui.py

# ─── Knowledge ───────────────────────────────────────────────────────────────

seed: ## Seed the PgVector knowledge base from knowledge_base/*.md
	uv run python scripts/seed_knowledge.py

# ─── Docker: app stack ───────────────────────────────────────────────────────

net: ## Create the shared docker network (idempotent)
	docker network create agent-net 2>/dev/null || true

up: net ## Bring up the app + postgres in Docker
	docker compose up --build -d

down: ## Tear down the app stack
	docker compose down

restart: ## Restart just the app container
	docker compose restart app

logs: ## Tail app logs
	docker compose logs -f app

ps: ## Show container status (app + langwatch)
	@docker compose ps; docker compose -f docker-compose.langwatch.yml ps 2>/dev/null || true

docker-build: ## Build the app image only
	docker build -t nsmtx-agent-template:latest .

# ─── Docker: LangWatch observability stack ──────────────────────────────────

langwatch-up: net ## Bring up the self-hosted LangWatch stack
	@test -f langwatch/.env || cp langwatch/.env.example langwatch/.env
	docker compose -f docker-compose.langwatch.yml up -d
	@echo "LangWatch UI → http://localhost:5560  —  run 'make langwatch-init' to auto-create the project + API key"

langwatch-init: ## [DEV] Auto-provision local account+org+project → LANGWATCH_API_KEY in .env
	bash scripts/langwatch_bootstrap.sh

langwatch-down: ## Tear down the LangWatch stack
	docker compose -f docker-compose.langwatch.yml down

# ─── Docker: Omni omnichannel hub (unofficial local validation) ─────────────

omni-up: net ## [VALIDATION] Bring up a local Automagik Omni (reuses agno Postgres; needs `make up`)
	docker compose -f docker-compose.omni.yml up -d
	@echo "Omni API → http://localhost:$(or $(OMNI_HOST_PORT),8882)/api/v2/docs  (seed key OMNI_API_KEY, default omni_sk_dev_local)"
	@echo "Port already used by a native Omni? Run: OMNI_HOST_PORT=8899 make omni-up"
	@echo "Next: register this agent as a webhook provider — see docs/omni.md"

omni-logs: ## Tail the Omni API logs
	docker compose -f docker-compose.omni.yml logs -f omni-api

omni-down: ## Tear down the Omni stack (the `omni` database persists in agno Postgres)
	docker compose -f docker-compose.omni.yml down

stack-up: up langwatch-up ## Everything: app + postgres + LangWatch

stack-down: down langwatch-down ## Tear everything down

# ─── Prod on a single VM (hardened compose override) ─────────────────────────

prod-up: net ## [VM] Hardened prod stack (loopback DB, restart, API_KEY required)
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

prod-down: ## [VM] Tear down the hardened prod stack
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# ─── Cloud deploy: AWS ECS Fargate via Copilot (see docs/deploy-aws.md) ──────

aws-secrets: ## Sync .env.production → SSM SecureString (copilot secrets)
	bash scripts/aws/env-sync.sh

aws-up: ## Provision + deploy to AWS ECS Fargate (first run ~15-25 min)
	bash scripts/aws/up.sh

aws-redeploy: ## Rebuild image + roll the ECS service (code-push path, minutes)
	bash scripts/aws/redeploy.sh

aws-logs: ## Tail the ECS service logs
	copilot svc logs --app $(or $(COPILOT_APP),nsmtx-agent) --name agent --env production --follow

aws-down: ## Tear down ALL AWS infra (app + Aurora — DATA LOST)
	bash scripts/aws/down.sh

# ─── Housekeeping & deploy ───────────────────────────────────────────────────

clean: ## Remove caches, build artifacts and the local SQLite db
	rm -rf .pytest_cache .ruff_cache dist build *.egg-info tmp/agent.db
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true

deploy: check ## Example VM deploy (pull, sync, restart via pm2) — adapt to your host
	git pull --ff-only
	uv sync --frozen || uv pip install -e .
	pm2 restart $(AGENT_ID) || pm2 start ecosystem.config.js
