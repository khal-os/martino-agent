# Agno — Basic Features Map

A cheat-sheet of the Agno primitives this template uses: for each, a one-line
**why**, the **how** (as used here), and a **📖 direct link** to the Agno docs for
the full story. Agno is a Python framework for building agents; its runtime
(**AgentOS**) is a FastAPI app that turns your agents into a REST platform.

> Version target: **Agno 2.x** (`agno[os]>=2.5,<3.0`). API paths below are for 2.x.
> Agno also ships an `AGENTS.md` + `.cursorrules` with coding conventions —
> 📖 <https://docs.agno.com/other/cursor-rules>

---

## 1. Agent
**Why:** the core unit — one object bundling a model, tools, instructions and
memory. **How:** `model + tools + instructions (+ db, knowledge, hooks, state)`.

```python
from agno.agent import Agent
from agno.models.anthropic import Claude

agent = Agent(
    model=Claude(id="claude-sonnet-5"),
    tools=[...],
    instructions="You are a helpful assistant.",
    markdown=True,
)
agent.print_response("hi", stream=True)   # dev
# await agent.arun("hi")                    # prod (returns RunOutput)
```
Key params: `model, tools, instructions, db, knowledge, search_knowledge,
session_state, add_session_state_to_context, add_history_to_context,
num_history_runs, enable_user_memories, pre_hooks, post_hooks, tool_hooks,
fallback_config, markdown, debug_mode`.
→ see `agents/assistant.py` · 📖 <https://docs.agno.com/agents/building-agents> · full param list <https://docs.agno.com/reference/agents/agent>

## 2. Models
**Why:** swap LLM providers without touching agent logic. **How:** one import per
provider — `agno.models.anthropic.Claude`, `openai.OpenAIChat`, `google.Gemini`,
`openrouter.OpenRouter`, `litellm.LiteLLMOpenAI` (proxy). Fallback across models
via `fallback_config` (see `models.py::build_fallback_config`).
→ see `models.py` · 📖 <https://docs.agno.com/agents/building-agents>

## 3. Tools
**Why:** let the model *act* — the LLM picks the args, your Python does the work.
**How:** any function is a tool; add `@tool(...)` for name/description/hooks; a tool
may take `run_context: RunContext` to read/write session state.

```python
from agno.tools import tool
from agno.run import RunContext

def add_item(run_context: RunContext, item: str) -> str:
    run_context.session_state.setdefault("cart", []).append(item)   # auto-persisted
    return "ok"
```
→ see `tools/example_tools.py` · 📖 write tools <https://docs.agno.com/tools/tool-decorator>

## 4. Session State
**Why:** carry data across turns (a cart, a wizard step) without a DB call in your
code. **How:** a per-session dict, **persisted to the db** and auto-reloaded by
`session_id`. `session_state={...}` sets defaults; tools mutate
`run_context.session_state` (auto-saved); `add_session_state_to_context=True` shows
it to the model; `enable_agentic_state=True` lets the model update it.
→ see `agents/assistant.py`, `tools/example_tools.py` · 📖 <https://docs.agno.com/basics/state/agent/overview>

## 5. Memory & History
**Why:** the agent remembers the conversation (history) and, optionally, durable
facts about a user (memories). **How:** `add_history_to_context=True` +
`num_history_runs=N` for conversation; `enable_user_memories=True` (keyed by
`user_id`) for long-term facts — costs tokens, so renan turns it OFF and uses
session state; eugenia keeps it ON.
→ 📖 memory best practices <https://docs.agno.com/memory/best-practices>

## 6. Db / Storage
**Why:** sessions, memory, state (and traces/knowledge) need to persist. **How:**
`PostgresDb(db_url=...)` (prod) or `SqliteDb(db_file=...)` (dev). House tip: split
sessions and traces into two DBs.
→ see `db.py` · 📖 sessions <https://docs.agno.com/basics/sessions/overview>

## 7. Knowledge (RAG)
**Why:** ground answers in your docs/policies instead of the model's memory.
**How:** content is embedded and searched by similarity; attach with
`Agent(knowledge=..., search_knowledge=True)` for agentic RAG. Alternative house
pattern: skip the vector store, give the agent filesystem read/grep tools over a
markdown folder (eugenia/renan) — simpler, no embedder key.
→ see `knowledge.py`, `scripts/seed_knowledge.py` · 📖 PgVector <https://docs.agno.com/knowledge/vector-stores/pgvector/overview>

## 8. Hooks (three kinds)
**Why:** enforce guardrails and cross-cutting logic around a run without polluting
tools. **How:**

| Hook | When | Injected params | Use |
|---|---|---|---|
| **pre_hooks** | after session load, before LLM | `run_input, agent, session, run_context, user_id` | validate/guard input, preload state; raise `InputCheckError` to halt |
| **post_hooks** | after LLM, before returning | `run_output, agent, session, run_context` | validate/redact/format output; raise `OutputCheckError` |
| **tool_hooks** | around every tool call | `function_name, function_call, arguments, run_context` | logging, timing, allowlist; must call `function_call(**arguments)` |

→ see `hooks/` · 📖 pre/post <https://docs.agno.com/hooks/overview> · tool hooks <https://docs.agno.com/tools/hooks>

## 9. AgentOS (serving)
**Why:** turn agents into a real product surface — a FastAPI app with 50+ REST
endpoints (runs, sessions, memory, knowledge, health). **How:**
```python
from agno.os import AgentOS
agent_os = AgentOS(agents=[agent], tracing=True)   # tracing=True → spans in the db
app = agent_os.get_app()      # hand to uvicorn
```
Bring-your-own FastAPI + middleware is supported (we add Bearer auth + `/health`).
→ see `main.py` · 📖 <https://docs.agno.com/agent-os/introduction>

## 10. Teams & Workflows (not used here)
**Why:** multi-agent collaboration (Team) or deterministic multi-step pipelines
(Workflow). **How:** eugenia and renan deliberately use a **single Agent + tools**
("LLM decides what, code decides how") instead — reach for Team/Workflow only when
one agent + tools genuinely can't express the control flow.
→ 📖 <https://docs.agno.com/agent-os/overview>

## 11. Observability
**Why:** see cost, latency, tool calls and failures per run. **How:** Agno emits
OpenTelemetry/OpenInference spans; this template wires two sinks:
- **Agno-native** — `AgentOS(tracing=True)` stores spans in the agents' own db
  (offline, free).
- **LangWatch** — the OpenInference instrumentor ships spans to a self-hosted
  LangWatch (traces, cost, custom events, scenarios).
→ see `observability.py`, [`observability.md`](observability.md) · 📖 <https://docs.agno.com/agent-os/introduction> (tracing)

## 12. Evals
**Why:** verify behavior against the real model, not just wiring. **How:** Agno's
`agno.eval` ships `ReliabilityEval` (did the right tools fire?) and
`AgentAsJudgeEval` (LLM judge vs a rubric). We wrap them in `evals/` (`make eval`).
→ see `evals/` · 📖 reference pattern: `agno-agi/agentos-docker-template` `evals/`
