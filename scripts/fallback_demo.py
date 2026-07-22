"""Live model-fallback demo: primary suffers a (simulated) 503 outage → fallback serves.

    make demo-fallback          # needs real keys for both providers in .env

How agno decides to fall back (agno/models/fallback.py):
  ✅ falls back on: 5xx, network errors, 429/529 rate limits, context overflow
  ❌ does NOT fall back on other 4xx (bad model id, malformed request, auth) —
     those are caller bugs; retrying them elsewhere would mask the bug.

So this demo simulates the *legitimate* trigger — a provider outage — by making
the primary model's ``response`` raise ``ModelProviderError(503)``. The fallback
(cross-provider, real API call) must serve the answer.

Known quirk: ``RunOutput.model`` keeps reporting the PRIMARY model id even when
the fallback served the run — assert on behavior/content, not on that field.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Cross-provider pair: survive a whole-provider outage, not just one model.
os.environ.setdefault("MODEL_PROVIDER", "anthropic")
os.environ.setdefault("MODEL_ID", "claude-sonnet-5")
os.environ.setdefault("FALLBACK_PROVIDER", "openai")
os.environ.setdefault("FALLBACK_MODEL_ID", "gpt-5.6-terra")

from agno.agent import Agent
from agno.exceptions import ModelProviderError

from agent_app import config

config.get_settings.cache_clear()
settings = config.get_settings()

from agent_app.models import build_fallback_config, build_model  # noqa: E402

primary = build_model(settings)


def synthetic_outage(*args, **kwargs):
    raise ModelProviderError(
        message="synthetic provider outage (demo)",
        status_code=503,
        model_name=primary.name,
        model_id=primary.id,
    )


primary.response = synthetic_outage  # type: ignore[method-assign]
primary.aresponse = synthetic_outage  # type: ignore[method-assign]

agent = Agent(model=primary, fallback_config=build_fallback_config(settings), markdown=False)
out = agent.run(input="Reply with exactly: FALLBACK-OK")

print(f"\nprimary:   {settings.model_id} (killed with synthetic 503)")
print(f"fallback:  {settings.fallback_provider}/{settings.fallback_model_id}")
print(f"response:  {str(out.content).strip()[:60]}")

ok = "FALLBACK-OK" in str(out.content)
print("RESULT:", "PASS — fallback took over" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
