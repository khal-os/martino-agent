"""Eval cases — the behavioral contract of each agent, as data.

Each case sends one input to one agent and checks up to two things (both are
Agno built-ins, community pattern from the official agentos-docker-template):

- **reliability** — ``ReliabilityEval``: did the agent call the tools we expect?
  (set ``expected_tool_calls``)
- **judge** — ``AgentAsJudgeEval``: does the response meet a rubric? Binary
  pass/fail scored by an LLM judge. (set ``criteria``)

Add a case per behavior you care about, then run ``make eval``.
Evals hit the real model — they cost tokens and belong in a nightly/pre-release
lane, not the unit-test loop (that's ``make test``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    """One eval case: an input to one registered agent + optional checks."""

    name: str
    agent_key: str  # key in agent_app.agents.BUILDERS
    input: str

    # Judge check (LLM judge against a rubric, binary pass/fail).
    criteria: str | None = None

    # Reliability check (tool-call assertion).
    expected_tool_calls: tuple[str, ...] | None = None
    allow_additional_tool_calls: bool = True


CASES: tuple[Case, ...] = (
    # Tool routing: a cart request must actually call the tool (not hallucinate).
    Case(
        name="assistant_adds_to_cart_via_tool",
        agent_key="assistant",
        input="Please add milk to my cart.",
        expected_tool_calls=("add_to_cart",),
        criteria=(
            "Confirms milk was added to the cart. Does not invent prices, "
            "discounts or other items the user didn't mention."
        ),
    ),
    # Multi-tool flow: add then view, in one turn.
    Case(
        name="assistant_add_then_view_cart",
        agent_key="assistant",
        input="Add bread to my cart and then show me everything in it.",
        expected_tool_calls=("add_to_cart", "view_cart"),
        criteria="The final answer lists the cart contents and includes bread.",
    ),
    # Grounding/persona: no tools needed; concise, honest scope description.
    # NOTE on judge criteria: binary LLM judges are somewhat noisy — keep rubrics
    # about VERIFIABLE properties (mentions X, doesn't claim Y) and avoid brittle
    # numeric rules like exact line counts, or the same answer can flip PASS/FAIL
    # between runs.
    Case(
        name="assistant_describes_scope_honestly",
        agent_key="assistant",
        input="What can you help me with?",
        criteria=(
            "Describes its actual capabilities (managing a shopping cart, telling "
            "the time, simple text operations and/or price lookups). "
            "Does not claim capabilities it doesn't have (no web browsing, no email, "
            "no calendar). Reasonably concise — a short list, not an essay."
        ),
    ),
    # A/B challenger arm (experiments/registry.py 'assistant-tone' variant B). Runs
    # the SAME contract against the concise persona: an offline regression gate to
    # clear a variant *before* you route live traffic to it (best practice — catch
    # regressions cheaply offline; measure real impact online). Same tool routing,
    # but the judge also holds it to its terser promise.
    Case(
        name="assistant_concise_variant_still_uses_tools",
        agent_key="assistant-concise",
        input="Please add milk to my cart.",
        expected_tool_calls=("add_to_cart",),
        criteria=(
            "Confirms milk was added to the cart. Notably brief — a sentence or two, "
            "no filler or preamble. Does not invent prices or items."
        ),
    ),
)
