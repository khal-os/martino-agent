"""Run the eval suite against the real model.

    python -m evals                 # all cases
    python -m evals --case NAME     # one case (substring match)
    python -m evals -v              # also print judge/reliability details

Exit code 0 = all passed; 1 = any failure. Requires a real model key in .env.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agno.eval import AgentAsJudgeEval, ReliabilityEval

from agent_app.agents import get_agents
from agent_app.config import get_settings
from agent_app.models import build_model
from evals.cases import CASES, Case


@dataclass
class Outcome:
    name: str
    reliability: bool | None = None  # None = check not configured
    judge: bool | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.reliability is not False and self.judge is not False


def run_case(case: Case, agents_by_key: dict, judge_model, verbose: bool) -> Outcome:
    outcome = Outcome(name=case.name)
    agent = agents_by_key.get(case.agent_key)
    if agent is None:
        outcome.error = f"unknown agent_key '{case.agent_key}'"
        return outcome
    try:
        response = agent.run(input=case.input, session_id=f"eval-{uuid4().hex[:8]}")

        if case.expected_tool_calls is not None:
            rel = ReliabilityEval(
                name=f"{case.name}:reliability",
                agent_response=response,
                expected_tool_calls=list(case.expected_tool_calls),
                allow_additional_tool_calls=case.allow_additional_tool_calls,
            ).run(print_results=verbose)
            try:
                rel.assert_passed()  # type: ignore[union-attr]
                outcome.reliability = True
            except (AssertionError, AttributeError):
                outcome.reliability = False

        if case.criteria is not None:
            judge = AgentAsJudgeEval(
                name=f"{case.name}:judge",
                criteria=case.criteria,
                scoring_strategy="binary",
                model=judge_model,
                print_results=verbose,
            ).run(input=case.input, output=str(response.content))
            # pass_rate is a PERCENTAGE (0–100); it's only set when the judge parsed
            # a result, so guard for the unparseable-output case.
            outcome.judge = bool(judge and getattr(judge, "pass_rate", 0.0) >= 100.0)
    except Exception as exc:  # noqa: BLE001 — report, don't crash the suite
        outcome.error = f"{type(exc).__name__}: {exc}"
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(prog="evals")
    parser.add_argument("--case", help="run only cases whose name contains this substring")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    # Judge with a distinct, stronger tier so the agent isn't grading itself
    # (self-judging inflates pass rates). Falls back to the primary if @high fails.
    try:
        judge_model = build_model(settings, model_id="@high")
    except Exception:  # noqa: BLE001
        judge_model = build_model(settings)

    # Registry keys align 1:1 with get_agents() order.
    from agent_app.agents import BUILDERS

    agents_by_key = dict(zip(BUILDERS.keys(), get_agents(), strict=True))

    selected = [c for c in CASES if not args.case or args.case in c.name]
    if not selected:
        print(f"No case matches '{args.case}'. Available: {[c.name for c in CASES]}")
        return 1

    print(f"Running {len(selected)} eval case(s) against model '{settings.model_id}'...\n")
    outcomes = [run_case(c, agents_by_key, judge_model, args.verbose) for c in selected]

    def mark(value: bool | None) -> str:
        return "—" if value is None else ("PASS" if value else "FAIL")

    width = max(len(o.name) for o in outcomes) + 2
    print(f"\n{'case'.ljust(width)}reliability  judge   result")
    for o in outcomes:
        result = "ERROR: " + o.error if o.error else ("PASS" if o.passed else "FAIL")
        print(
            f"{o.name.ljust(width)}{mark(o.reliability).ljust(13)}{mark(o.judge).ljust(8)}{result}"
        )

    failed = [o for o in outcomes if not o.passed]
    print(f"\n{len(outcomes) - len(failed)}/{len(outcomes)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
