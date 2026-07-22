# Prompt Caching

Anthropic prompt caching stores a **prefix** of the request and re-serves it
cheaply on the next call (input tokens billed at ~1/10th; faster TTFT). The
prefix is your **system prompt**. This is the single biggest cost lever for a
chat agent with a large, stable prompt.

> Real number from `genie-hv-eugenia`: caching a ~25k-token system prompt cut
> Sonnet input cost from **$3 → $0.30 / Mtok** — a 10× reduction — plus lower
> latency.

## The rule that makes it work
The cache only hits when the prefix is **byte-identical** to the previous call.
So:

| Content | Where it goes | Why |
|---|---|---|
| Persona, rules, tool defs, output format | **system prompt** (cached) | never changes → cache stays warm |
| Today's date, per-user dossier, session context, memories | **message 0** (after the cache breakpoint) | changes per turn/user → would bust the cache if in the prefix |

Put anything volatile in the *system prompt* and every change rewrites the whole
cached prefix — you pay the 25k tokens again **and** pay the cache-write premium.

## How this template implements it

1. **Enable caching on the model** — `models.py`:
   ```python
   Claude(id=..., cache_system_prompt=True, extended_cache_time=True)  # 1h TTL
   ```
2. **Keep the system prompt static** — `agents/assistant.py`:
   ```python
   add_datetime_to_context=False,   # don't let Agno inject a live clock into the prefix
   ```
   (Also relevant: `add_memories_to_context=False` if you use user memories — eugenia
   found each new memory rewrote the cached prefix, writing 44× more cache.)
3. **Inject volatile context as message 0** — `context.py`:
   ```python
   additional_input=[build_context_message(settings)]   # date etc., AFTER the system prompt
   ```

## Two granularities of "volatile"

- **Process-stable (the date):** frozen at day granularity via `BUILD_DATE`
  (default `date.today()` at boot). The prefix + message-0 stay stable for the
  whole process; a redeploy rolls the date forward. This is eugenia's approach and
  the template default.

- **Per-request (a specific user's context):** pass a fresh message-0 per run so
  the *system prompt cache stays shared across all users*:
  ```python
  from agent_app.context import run_with_context
  run_with_context(agent, "quero remarcar", {"nome": "Ana", "plano": "PME"})
  # → agent.run(input=[<runtime_context msg>, <user msg>])
  ```
  Never fold per-user data into the system prompt — it would give every user a
  private, never-hit cache entry.

## Verifying cache hits
Check the response usage: Anthropic returns `cache_creation_input_tokens` (first
call, the write) and `cache_read_input_tokens` (subsequent hits). A healthy agent
shows high `cache_read` and near-zero `cache_creation` after warm-up. LangWatch
surfaces token/cost breakdowns per trace (see observability.md).

## Learn more
- **Anthropic prompt caching** (how the prefix cache + TTL work) —
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- **Agno prompt caching** (`cache_system_prompt`, `extended_cache_time` on `Claude`) —
  https://docs.agno.com/agents/building-agents
