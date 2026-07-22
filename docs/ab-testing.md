# A/B testing agents (multi-variant experiments)

Route live traffic between two or more agent **variants** (A/B, or A/B/C/D/E…),
control the split **remotely without a redeploy**, and measure each arm in
LangWatch. This is a first-class, production-shaped feature of the template — not
a toy — distilled from how the feature-flag world routes traffic and how the
LLM-observability world measures it.

> TL;DR: `POST /experiments/{id}/run` buckets the caller to a sticky variant and
> runs it; `PUT /experiments/{id}/allocation` changes the split at runtime;
> `GET /experiments/{id}` monitors it; every run is stamped with `ab.variant` so
> you slice quality/latency/tokens/cost per arm in LangWatch.

---

## Why this exists (what the market does)

We researched the landscape before building. The short version:

- **Neither Agno nor LangWatch ships an online A/B primitive.** Agno steers you
  toward registering each variant as a distinct `Agent`; LangWatch's own A/B doc
  splits with client-side `Math.random()` and tags the trace. So the *routing
  plane* is yours to build.
- **Feature-flag platforms** (GrowthBook, Unleash, Statsig, LaunchDarkly AI
  Configs) are the ones that actually do production traffic splitting — via
  **deterministic, sticky bucketing**: hash a stable id + a per-experiment salt
  into a bucket, map buckets to variants by weight. We copy that.
- **LLM-observability platforms** (LangWatch, Langfuse, Braintrust) are the
  *measurement plane*: they don't route, but they let you **slice metrics by a
  variant tag** on the trace. We hand measurement to LangWatch.

So this feature = **flag-world routing + LangWatch measurement**, wired natively
into AgentOS. See the comparison table and citations at the bottom.

| Layer | Who does it well | What we borrowed |
|---|---|---|
| Sticky traffic split | GrowthBook / Unleash / Statsig | FNV-1a deterministic bucketing (`bucketing.py`) |
| Runtime % control | LaunchDarkly / Statsig | file-backed override store, hot-reload (`store.py`) |
| Per-variant metrics | LangWatch / Langfuse | `ab.variant` trace attribute → slice in the UI |
| Prompt/version pinning | LangWatch / Langfuse | `Variant.version`, stamped on the trace |

---

## Architecture — four planes

```
src/agent_app/experiments/
├── bucketing.py   # PURE maths: deterministic, sticky, N-way, coverage/holdout
├── registry.py    # the git-versioned "folder of variants" + baseline weights
├── store.py       # live, remotely-tunable weights + monitor counters (file-backed)
├── engine.py      # assign_variant(): registry ⊕ store ⊕ bucketing → Assignment
└── routes.py      # run / control / monitor HTTP endpoints
```

- **registry** is the source of truth in git: what experiments exist, their
  variants (each → a registered agent id), and *baseline* weights.
- **store** holds *runtime overrides* an operator sets via the API. Effective
  allocation = `baseline ⊕ override`.
- **engine** ties them together with the pure bucketing maths.
- A variant is a **normally-registered Agno agent** (stable `id`), so each arm is
  also reachable directly at `/agents/{id}/runs` for debugging, and a session
  carries across arms (they share one db — the model-switch pattern).

### The shipped example

`experiments/registry.py` defines **`assistant-tone`**: A (the default, detailed
`assistant`) vs B (`assistant-concise`, a terser persona). Same tools, db and
hooks — different system prompt (`prompts/assistant/concise.md`). B is registered
in `agents/__init__.py` as `assistant-concise` via the same builder with a
different id + prompt. Realistic question it answers: *does a terser bot win on
satisfaction without hurting task success?* Primary metric = 👍 rate; guardrails
= tokens/latency/cost — all sliced by `ab.variant` in LangWatch.

---

## Using it

All endpoints sit behind the Bearer-auth middleware — add
`-H "Authorization: Bearer $API_KEY"` when `API_KEY` is set.

### Run a variant (sticky per user)
```bash
curl -X POST localhost:8888/experiments/assistant-tone/run \
  -H "Content-Type: application/json" \
  -d '{"message": "how much is milk?", "user_id": "user-123"}'
```
```jsonc
{
  "experiment": "assistant-tone",
  "variant": "B",                    // which arm served it
  "agent_id": "assistant-concise",
  "variant_version": "2026-07-16",   // the exact version — also on the trace
  "reason": "bucketed",              // bucketed | forced | disabled | holdout
  "sticky": true,                    // false = no stable id, random (won't stick)
  "session_id": "user-123",
  "run_id": "…",
  "content": "milk: R$4.50"
}
```
`user-123` gets the **same** arm on every request. Prefer always sending a
`user_id`; without one it falls back to `session_id`, then to a random (non-sticky)
id. Force a specific arm for testing with `"variant": "A"`.

### Control the split remotely (no redeploy)
```bash
# Ramp B from 50% to 80%:
curl -X PUT localhost:8888/experiments/assistant-tone/allocation \
  -H "Content-Type: application/json" \
  -d '{"weights": {"A": 0.2, "B": 0.8}}'

# Canary: only 5% of users are eligible (rest get control A):
curl -X PUT localhost:8888/experiments/assistant-tone/allocation \
  -d '{"coverage": 0.05}'

# Pause the experiment (everyone → control):
curl -X PUT localhost:8888/experiments/assistant-tone/allocation \
  -d '{"enabled": false}'
```
Overrides persist to `EXPERIMENTS_STORE_PATH` (default `tmp/experiment_allocations.json`)
and take effect immediately. Delete that row / file to revert to the git baseline.

### Monitor it
```bash
curl localhost:8888/experiments/assistant-tone
```
```jsonc
{
  "key": "assistant-tone",
  "enabled": true, "coverage": 1.0,
  "source": "override",              // baseline | override
  "variants": [
    {"name": "A", "agent_id": "assistant",         "version": "2026-07-16",
     "weight": 0.2, "assignments": 41, "observed_share": 0.21},
    {"name": "B", "agent_id": "assistant-concise", "version": "2026-07-16",
     "weight": 0.8, "assignments": 156, "observed_share": 0.79}
  ],
  "total_assignments": 197
}
```
`GET /experiments` lists them all. **`assignments`/`observed_share` are an
in-process sanity gauge only** (they reset on restart, don't aggregate across
workers/pods). The real metrics are in LangWatch — see below.

### Preview an assignment (dry-run, no run, no count)
```bash
curl -X POST localhost:8888/experiments/assistant-tone/assign \
  -d '{"user_id": "user-123"}'      # → which arm would this user get?
```

---

## Measurement — LangWatch is the plane

Each run through the experiment endpoint stamps the trace (via the
`tag_experiment` pre-hook → `observability.tag_experiment`) with:

```
ab.experiment       = "assistant-tone"
ab.variant          = "B"
ab.variant_version  = "2026-07-16"
```

In the LangWatch UI, **group/filter by `ab.variant`** to compare arms on latency,
tokens, cost and any quality score — that's the whole point, and it's why the
in-process counters are just a gauge. Tie product outcomes to the arm by posting
`👍/👎` to `POST /feedback` with the run's `trace_id` (see `routes.py`); those
events land on the same trace you can slice by variant.

Pre-launch, gate a new variant offline first: `evals/cases.py` has a case for
`assistant-concise` (`make eval`). Best practice is **offline eval (cheap
regression gate) → online experiment (real impact)**.

---

## Add a variant / a new experiment

**Add an arm to an existing experiment:**
1. Register the variant agent in `agents/__init__.py:BUILDERS` (e.g. a new prompt
   via the parametrized `build_assistant`, or a different model — see below).
2. Add a `Variant(name=…, agent_id=…, weight=…, version=…)` row to the experiment
   in `experiments/registry.py`.
3. Add an offline eval case for it in `evals/cases.py`.

**A variant can differ by anything the builder varies:**
- **Prompt** (the example): `partial(build_assistant, agent_id=…, prompt_variant="concise")`.
- **Model**: give the builder a different `model_id` (e.g. `@fast` vs `@medium`) —
  this is the cost/latency-vs-quality experiment; those guardrails show up directly
  in the LangWatch slice.
- **Tools / hooks / retrieval**: any wiring difference — it's a whole agent.

**A new experiment:** add an `Experiment(...)` to `EXPERIMENTS`. `key` is also the
hashing salt — treat it as immutable once live (renaming re-randomizes everyone).
Pick `unit="user"` (default) or `"session"` for the sticky dimension.

---

## How the bucketing works (and why it's sticky)

`bucketing.py` uses **FNV-1a 32-bit, GrowthBook "hash version 2"** (double-hash):

```
bucket(unit_id, salt) = fnv1a(str(fnv1a(salt + unit_id))) % 10000 / 10000   → [0,1)
```

Then walk the cumulative variant weights and pick the arm the bucket lands in.
Properties this buys us:

- **Sticky:** same `unit_id` + same experiment `key` → same bucket → same arm,
  forever. No `random` (which would flip a user's arm every request and ruin
  within-user metrics).
- **Independent experiments:** the per-experiment salt means the same user lands
  in *uncorrelated* buckets across concurrent experiments.
- **Monotonic ramp:** raising an arm's weight only pulls in *additional* users;
  already-assigned users keep their arm (grow weight at the boundary, don't
  reorder variants).
- **N-way native:** pass more variants/weights for C/D/E.
- **Coverage/holdout:** an independent gate hash reserves a canary slice.

Why FNV-1a and not Murmur/SHA-256: ~5 lines, no dependency, clean `[0,1)` float.
Unleash (MurmurHash3) and Statsig (SHA-256) are equally valid, just more code.

---

## Best practices (baked in / recommended)

- **Sticky assignment by a stable id** — always pass `user_id`. ✅ built in.
- **Guardrail metrics, not just quality** — track latency p50/p95, cost/req,
  error/refusal rate alongside the primary metric. A quality win that doubles
  cost usually isn't shippable. Slice all of them by `ab.variant`.
- **Offline + online** — regression-gate a variant on a dataset (`make eval`)
  before routing live traffic; measure real impact online.
- **Canary then ramp** — start B at a low weight or low `coverage`, watch
  guardrails, then raise. ✅ supported.
- **Size the test & don't peek** — LLM outputs are high-variance, so you need
  more samples than typical UI experiments; pre-register the analysis point or
  use a sequential test. Stopping the moment it looks significant inflates false
  positives.
- **Keep the judge fixed** — if an LLM-as-judge scores both arms, hold its
  model+prompt constant for the whole experiment, or you contaminate the compare.
  Sample online scoring (1–10%) since it's itself an LLM call.

---

## Production caveats (read before you ship)

- **Multi-pod scope.** The override store is a JSON file: shared across uvicorn
  workers on **one host**, not across machines/pods. For multi-pod k8s, back it
  with your DB / Redis / a flag provider (LaunchDarkly AI Configs, Statsig).
  `store.py` is the seam — swap its `load`/`save`. Same for the monitor counters
  (per-process) — LangWatch is the cross-instance truth.
- **No auto-promotion.** Deciding a winner and promoting it is a human step here
  (change the baseline weights in git, or the override). We found no verified
  first-party "auto-promote the winner" in LangWatch; don't assume one.
- **Streaming.** The `/run` endpoint is non-streaming for simplicity. For SSE,
  either hit the arm's native `/agents/{id}/runs` after calling `/assign`, or add
  a streaming variant of the route.

---

## References

Routing / bucketing:
- GrowthBook — build-your-own SDK (FNV-1a, hashVersion 2): <https://docs.growthbook.io/lib/build-your-own> · A/B testing LLMs: <https://www.growthbook.io/insights/ab-testing-llms>
- Unleash — stickiness / MurmurHash3: <https://docs.getunleash.io/concepts/stickiness>
- Statsig — how evaluation works (SHA-256 bucketing) / AI prompt experiments: <https://docs.statsig.com/sdks/how-evaluation-works> · <https://www.statsig.com/blog/ai-prompt-experiments/>
- LaunchDarkly AI Configs (runtime prompt/model control) / online evals: <https://launchdarkly.com/blog/ai-configs-ga-runtime-control-prompts-models/> · <https://launchdarkly.com/docs/tutorials/when-to-add-online-evals>

Measurement:
- LangWatch — A/B testing: <https://langwatch.ai/docs/prompt-management/features/advanced/a-b-testing> · capturing metadata: <https://langwatch.ai/docs/integration/python/tutorials/capturing-metadata> · link prompt to traces: <https://langwatch.ai/docs/prompt-management/features/advanced/link-to-traces>
- Langfuse — A/B via prompt labels: <https://langfuse.com/docs/prompt-management/features/a-b-testing>
- Braintrust — A/B testing LLM prompts (pattern): <https://www.braintrust.dev/articles/ab-testing-llm-prompts>

Agno:
- AgentOS API: <https://docs.agno.com/agent-os/using-the-api> · switching models: <https://docs.agno.com/faq/switching-models> · hooks: <https://docs.agno.com/hooks/overview>
