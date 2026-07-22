"""LangWatch experiment — batch-evaluate the agent over a dataset, logged to the UI.

Complements the offline ``python -m evals`` (agno judges, terminal-only): this
sends a full run + per-item metrics to LangWatch → **Experiments**, so results are
versioned, comparable across runs, and shareable. It also shows how to call a
LangWatch **built-in evaluator** (server-side) alongside your own checks.

    make experiment            # needs LANGWATCH_* + a model key in .env

Hits the real model — a nightly/pre-release lane, not the unit-test loop.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import langwatch
from langwatch.evaluation import init as evaluation_init

from agent_app.agents import get_agent
from agent_app.config import get_settings

# Tiny in-code dataset. Real projects load a versioned dataset (csv / LangWatch dataset).
DATASET = [
    {"id": "cart-milk", "input": "add milk to my cart", "expect_tool": "add_to_cart"},
    {"id": "cart-view", "input": "what's in my cart?", "expect_tool": "view_cart"},
    {"id": "price", "input": "how much is milk?", "expect_tool": "lookup_price"},
    {"id": "scope", "input": "send an email to my boss", "expect_tool": None},
]


def _tools_called(run_response) -> set[str]:
    tools = getattr(run_response, "tools", None) or []
    names = set()
    for t in tools:
        name = getattr(t, "tool_name", None) or (
            t.get("tool_name") if isinstance(t, dict) else None
        )
        if name:
            names.add(name)
    return names


def main() -> int:
    settings = get_settings()
    # Evals talk to LangWatch DIRECTLY (offline tooling — outside the connector
    # contract, which only covers the serving agent's traces/events). The agent
    # itself never sees these variables; they live only in the eval operator's env.
    endpoint = os.getenv("LANGWATCH_ENDPOINT")
    api_key = os.getenv("LANGWATCH_API_KEY")
    if not endpoint or not api_key:
        print("Set LANGWATCH_ENDPOINT and LANGWATCH_API_KEY to run experiments.")
        return 2
    langwatch.setup(api_key=api_key, endpoint_url=endpoint)
    agent = get_agent(settings.agent_id)

    evaluation = evaluation_init("agent-template-cart-suite")

    for i, row in enumerate(evaluation.loop(iter(DATASET))):
        idx = row["id"]
        resp = agent.run(input=row["input"], session_id=f"exp-{idx}")
        called = _tools_called(resp)

        # Our own reliability metric: was the expected tool used (or correctly avoided)?
        if row["expect_tool"] is None:
            passed = "add_to_cart" not in called and "lookup_price" not in called
        else:
            passed = row["expect_tool"] in called
        evaluation.log(
            "expected_tool_used",
            index=i,
            passed=passed,
            data={"id": idx, "input": row["input"], "tools_called": sorted(called)},
        )

        # A LangWatch built-in evaluator, server-side (no local model needed).
        # Flags PII leaking into the agent's output.
        try:
            evaluation.run(
                "presidio/pii_detection",
                index=i,
                data={"output": str(resp.content)},
                settings={"entities": {"EMAIL_ADDRESS": True, "PHONE_NUMBER": True}},
            )
        except Exception:  # noqa: BLE001 — evaluator id/availability varies by instance
            pass

    print("Experiment submitted → LangWatch UI → Experiments → 'agent-template-cart-suite'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
