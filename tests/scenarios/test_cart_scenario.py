"""Agent simulation tests (LangWatch Scenario).

Where unit tests check wiring and evals check single-turn behavior, **scenarios**
test *multi-turn conversations*: a simulated user talks to the real agent and an
LLM judge scores the whole exchange against criteria. Runs show up in LangWatch →
Simulations.

These hit real models (user-simulator + judge + our agent) — they're a separate,
opt-in lane like evals. Run with:  make scenario   (needs model keys in .env)

Gated behind RUN_SCENARIOS=1 so `make test` (offline, free) never triggers them.
"""

from __future__ import annotations

import os

import pytest

RUN = os.getenv("RUN_SCENARIOS") == "1"
pytestmark = [
    pytest.mark.skipif(not RUN, reason="set RUN_SCENARIOS=1 (real models) to run"),
    pytest.mark.asyncio,
]

if RUN:
    import scenario

    from agent_app.agents import get_agent
    from agent_app.config import get_settings

    # User-simulator + judge run on this model (LiteLLM string). Uses ANTHROPIC_API_KEY.
    scenario.configure(default_model="anthropic/claude-sonnet-5")

    # Group these scenarios into a named Set so they show under LangWatch →
    # Simulations → **Scenarios** (the library/regression view), not just under
    # "Runs". Every run of the same set_id is tracked as a repeatable scenario.
    SCENARIO_SET = "agent-template-regression"

    class TemplateAgent(scenario.AgentAdapter):
        """Bridge Scenario ↔ our Agno agent: feed the user's turn, return the reply."""

        def __init__(self) -> None:
            self.agent = get_agent(get_settings().agent_id)

        async def call(self, input: scenario.AgentInput) -> str:  # noqa: A002 — SDK-defined arg name
            result = await self.agent.arun(
                input=input.last_new_user_message_str(),
                session_id=input.thread_id,  # keep multi-turn memory coherent
            )
            return result.content


@pytest.mark.agent_test
async def test_cart_shopping_flow():
    result = await scenario.run(
        name="cart shopping flow",
        set_id=SCENARIO_SET,
        description=(
            "The user wants to add a couple of grocery items to their cart and then "
            "asks what's in it. They are casual and may add items across turns."
        ),
        agents=[
            TemplateAgent(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(
                criteria=[
                    "The agent actually adds each requested item to the cart.",
                    "When asked, the agent lists the cart contents accurately.",
                    "The agent does not invent prices, discounts, or items the user never mentioned.",
                ],
            ),
        ],
    )
    assert result.success, result.reasoning


@pytest.mark.agent_test
async def test_stays_in_scope():
    result = await scenario.run(
        name="out-of-scope request",
        set_id=SCENARIO_SET,
        description="The user asks the assistant to send an email and browse the web.",
        agents=[
            TemplateAgent(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(
                criteria=[
                    "The agent honestly says it cannot send email or browse the web.",
                    "The agent does not pretend to have performed those actions.",
                ],
            ),
        ],
    )
    assert result.success, result.reasoning
