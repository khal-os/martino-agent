# LangWatch — what we use, what we could add

LangWatch is broad (observability, evals, prompts, simulations, gateway). This is
the honest map for the template: what's **wired in**, what's **available to plug**,
and what's **out of scope** for a starter.

## ✅ Wired in (working, validated)

| Feature | How | Where | Run |
|---|---|---|---|
| **Tracing** (auto) | OpenInference `AgnoInstrumentor` → every run/LLM/tool span | `observability.py` | on when `CONNECTOR_REGISTER_URL` + `_TOKEN` are set |
| **Custom spans** | plain OTel tracer + `openinference.span.kind` around sub-steps | `tools/example_tools.py::lookup_price` | ↑ |
| **Rich metadata** | Resource (service/version/env/model/domain) + per-request span attrs (user/thread/`channel.*`/`ab.*`/`app.*`; channel via omni or `X-Channel-*` headers) | `observability.py`, `hooks/pre_hooks.py::enrich_trace`, `middleware.py` | ↑ |
| **Custom user events** | connector `events` link → `/api/track_event` (👍/👎, ratings, "converted") | `observability.py::track_event`, `POST /feedback` | app endpoint |
| **Scenario simulations** | simulated user + LLM judge, multi-turn → Simulations tab | `tests/scenarios/` | `make scenario` |
| **Batch experiments** | dataset run + metrics + built-in evaluator → Experiments tab | `evals/langwatch_experiment.py` | `make experiment` |

Plus **agno-native tracing** (`AgentOS(tracing=True)`) — spans in the app's own DB,
independent of LangWatch. Two complementary layers.

## 🔌 Available to plug (documented, not wired by default)

- **Online / real-time evaluations & guardrails** — score or *block* live traces
  server-side (PII, jailbreak, toxicity). Configure per-project in the UI, or call
  `evaluation.run("<evaluator_id>", …, as_guardrail=True)`. We show the offline
  call in `langwatch_experiment.py`; turning on live guardrails is a UI/policy step.
- **Prompt management CLI** (`langwatch prompt init/create/sync`) — prompts as
  versioned YAML synced to the server, with A/B + `langwatch.prompts.get(...)` at
  runtime. **We deliberately use file-based prompts** (`prompts/<agent>/system.md`
  + `prompt_loader.py`): git-native, zero-dependency, reviewable in PRs. LangWatch
  prompt mgmt is the upgrade when you want non-devs editing prompts or server-side
  A/B — adopt by swapping `prompt_loader.load_prompt()` for `langwatch.prompts.get()`.
- **Annotations & datasets** — human labeling of traces → datasets that feed
  experiments/scenarios. Pure UI workflow; nothing to code.

## 🚫 Out of scope for the template

- **DSPy visualization** — only relevant if you build DSPy-optimized programs; we
  don't. `langwatch.dspy` exists if you adopt DSPy later.
- **AI Gateway / Usage / budgets** — LangWatch-as-proxy features; orthogonal to a
  single-service starter.

## The QA ladder (how the pieces fit)

```
make test        offline, free, every commit   → wiring, tools, hooks, prompts
make eval        real model, cheap-ish         → single-turn behavior (agno judges)
make scenario    real model, multi-turn        → conversations (sim user + judge) → LangWatch
make experiment  real model, dataset           → batch metrics + built-in evaluators → LangWatch
```
Start at `test`; add `eval` cases for behaviors you rely on; add `scenario` for
multi-turn flows; use `experiment` for dataset regression + PII/safety evaluators.

## Docs (deep links)
- Python SDK & tracing — https://langwatch.ai/docs/integration/python/guide
- Custom user events (`/api/track_event`) — https://langwatch.ai/docs/user-events/custom
- Evaluations & experiments (`langwatch.evaluation`) — https://langwatch.ai/docs/evaluations/overview
- Agent simulations (Scenario) — https://langwatch.ai/docs/agent-simulations/getting-started
- Prompt management CLI (the pluggable alternative to our file-based prompts) —
  https://langwatch.ai/docs/prompt-management/cli
