# Adding a New Agent

The template is multi-agent by design: one module per agent + a registry.
Adding an agent is a 5-file checklist, ~15 minutes.

## 1. Create the prompt (versioned file, not a string literal)
```
src/agent_app/prompts/<name>/system.md
```
Static content only — persona, rules, output format. Anything volatile (date,
user data) is injected as message-0 via `context.py` (see docs/prompt-caching.md).
Optional per-model variant: `prompts/<name>/variants/<model-slug>/system.md`.

## 2. Create the agent module
Copy `src/agent_app/agents/assistant.py` → `src/agent_app/agents/<name>.py`:

```python
def build_<name>(settings: Settings) -> Agent:
    return Agent(
        id="<name>",                                   # hardcode the id
        name="<Display Name>",
        model=build_model(settings),
        db=build_db(settings),                          # shared session store
        tools=[...your tools...],
        tool_hooks=[logging_tool_hook],
        pre_hooks=PRE_HOOKS,                            # or agent-specific gates
        post_hooks=POST_HOOKS,
        instructions=load_prompt("<name>", agent_name="<Display Name>"),
        add_datetime_to_context=False,                  # keep the cached prefix stable
        additional_input=[build_context_message(settings)],
        add_history_to_context=True,
        num_history_runs=10,
        markdown=True,
    )
```

## 3. Register it
`src/agent_app/agents/__init__.py`:
```python
from .<name> import build_<name>
BUILDERS = {
    "assistant": build_assistant,
    "<name>": build_<name>,          # ← one line; AgentOS/evals/health pick it up
}
```

## 4. Add tools (if new ones)
`src/agent_app/tools/<name>_tools.py` — plain deterministic functions.
Rule: **the LLM decides what, code decides how.** Tools return small structured
results; secrets/retries/payloads live in code.

## 5. Add QA
- **Unit test** (offline, `make test`): test each new tool/hook directly —
  see `tests/test_tools.py`.
- **Eval case** (real model, `make eval`): add a `Case` in `evals/cases.py`
  with `expected_tool_calls` (reliability) and/or `criteria` (LLM judge).

## Done — verify
```bash
make check          # lint + offline tests
make dev            # GET /health → your agent id appears in "agents"
make eval-case CASE=<name>
```

## Learn more
- **Agno — building agents** (all `Agent` params) — https://docs.agno.com/agents/building-agents
- **This template's layout & house patterns** — [architecture.md](architecture.md)
- **Prompt caching / message-0** (why the date/context lives outside the system prompt) —
  [prompt-caching.md](prompt-caching.md)
