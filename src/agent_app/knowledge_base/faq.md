# Sample Knowledge Base — FAQ

Replace this with your real docs. Each markdown file under `knowledge_base/` is
chunked, embedded and inserted into PgVector by `scripts/seed_knowledge.py`.

## What is this template?
A production-shaped template for building agents on the Agno framework, following
the patterns used by Namastex agents (eugenia, renan): AgentOS + tools + hooks +
knowledge + Postgres + Docker.

## How does the agent use this knowledge?
When `KNOWLEDGE_ENABLED=1`, the agent can search this content via semantic
(hybrid) similarity and cite it in answers — "agentic RAG". With knowledge
disabled, the agent still runs; it just has no document lookup.

## How do I add a tool?
Write a plain Python function in `src/agent_app/tools/`, add it to `EXAMPLE_TOOLS`
(or your own list), and the model can call it. Keep the real logic in code.

## How do I deploy?
`docker compose up` for local (app + Postgres with pgvector), or build the image
and run it anywhere. See the README and `docs/architecture.md`.
