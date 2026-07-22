"""HTTP surface for the experiments feature — run, control, monitor.

Layered on AgentOS like the other custom routes (see ../routes.py). All of these
sit behind the Bearer-auth middleware, so remote control is authenticated.

Endpoints:
  * ``POST /experiments/{id}/run``          — bucket the caller → run the assigned
                                              variant → stamp the trace → return
                                              which arm served it.
  * ``GET  /experiments``                   — list experiments + effective allocation.
  * ``GET  /experiments/{id}``              — monitor one: config, live weights,
                                              per-arm assignment counts, observed split.
  * ``PUT  /experiments/{id}/allocation``   — remote traffic control: set weights /
                                              enable / coverage with no redeploy.
  * ``POST /experiments/{id}/assign``       — dry-run: which arm would this id get?
                                              (sticky-assignment debugging; no run.)

Why a dedicated endpoint and not the native ``/agents/{id}/runs``: the agent id is
in that URL, so it can't route between arms. This front door does the routing and
dispatches to the chosen registered agent — reusing the exact same agents AgentOS
serves directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agents import get_agent
from ..config import Settings
from .engine import Assignment, assign_variant, resolve_unit_id
from .registry import EXPERIMENTS, Experiment, get_experiment
from .store import AllocationStore, get_store


class RunIn(BaseModel):
    message: str
    user_id: str | None = None
    session_id: str | None = None
    variant: str | None = None  # force a specific arm (testing); bypasses bucketing


class AssignIn(BaseModel):
    user_id: str | None = None
    session_id: str | None = None


class AllocationIn(BaseModel):
    weights: dict[str, float] | None = None  # must cover exactly the current arms
    enabled: bool | None = None
    coverage: float | None = None


def _allocation_view(experiment: Experiment, store: AllocationStore) -> dict[str, Any]:
    """Config + effective allocation + live counts for one experiment."""
    alloc = store.resolve(experiment)
    counts = store.counts(experiment.key)
    total = sum(counts.values())
    return {
        "key": experiment.key,
        "description": experiment.description,
        "unit": experiment.unit,
        "enabled": alloc.enabled,
        "coverage": alloc.coverage,
        "source": "override" if alloc.overridden else "baseline",
        "variants": [
            {
                "name": v.name,
                "agent_id": v.agent_id,
                "version": v.version,
                "description": v.description,
                "weight": round(w, 4),
                "assignments": counts.get(v.name, 0),
                "observed_share": round(counts.get(v.name, 0) / total, 4) if total else None,
            }
            for v, w in zip(experiment.variants, alloc.weights, strict=True)
        ],
        "total_assignments": total,
    }


def _assignment_payload(assignment: Assignment) -> dict[str, Any]:
    return {
        "experiment": assignment.experiment,
        "variant": assignment.variant,
        "agent_id": assignment.agent_id,
        "variant_version": assignment.version,
        "reason": assignment.reason,
        "sticky": assignment.sticky,
        "overridden": assignment.overridden,
    }


def register_experiment_routes(app: FastAPI, settings: Settings) -> None:
    """Wire the experiment endpoints onto ``app``."""
    store = get_store(settings.experiments_store_path)

    @app.get("/experiments")
    def list_experiments() -> JSONResponse:
        return JSONResponse(
            {"experiments": [_allocation_view(exp, store) for exp in EXPERIMENTS.values()]}
        )

    @app.get("/experiments/{experiment_id}")
    def get_experiment_route(experiment_id: str) -> JSONResponse:
        try:
            experiment = get_experiment(experiment_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(_allocation_view(experiment, store))

    @app.put("/experiments/{experiment_id}/allocation")
    def set_allocation(experiment_id: str, body: AllocationIn) -> JSONResponse:
        try:
            experiment = get_experiment(experiment_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        try:
            store.set_allocation(
                experiment,
                weights=body.weights,
                enabled=body.enabled,
                coverage=body.coverage,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(_allocation_view(experiment, store))

    @app.post("/experiments/{experiment_id}/assign")
    def assign(experiment_id: str, body: AssignIn) -> JSONResponse:
        try:
            experiment = get_experiment(experiment_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        unit_id, sticky = resolve_unit_id(experiment, body.user_id, body.session_id)
        assignment = assign_variant(experiment, unit_id, store, record=False)
        payload = _assignment_payload(assignment) | {"sticky": sticky, "unit_id": unit_id}
        return JSONResponse(payload)

    @app.post("/experiments/{experiment_id}/run")
    async def run_experiment(experiment_id: str, body: RunIn) -> JSONResponse:
        try:
            experiment = get_experiment(experiment_id)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

        unit_id, sticky = resolve_unit_id(experiment, body.user_id, body.session_id)
        try:
            assignment = assign_variant(experiment, unit_id, store, forced=body.variant)
        except KeyError as exc:  # forced an unknown variant
            return JSONResponse({"error": str(exc)}, status_code=422)

        try:
            agent = get_agent(assignment.agent_id)
        except KeyError as exc:  # variant points at an unregistered agent (config bug)
            return JSONResponse({"error": f"variant misconfigured: {exc}"}, status_code=500)

        # The assignment travels as run metadata; the tag_experiment pre-hook reads
        # it and stamps ab.* on the trace (hooks/pre_hooks.py). session_id defaults
        # to the unit id so a user's turns share one conversation.
        result = await agent.arun(
            input=body.message,
            user_id=body.user_id,
            session_id=body.session_id or unit_id,
            stream=False,
            metadata={
                "ab_experiment": assignment.experiment,
                "ab_variant": assignment.variant,
                "ab_variant_version": assignment.version,
            },
        )

        payload = _assignment_payload(assignment) | {
            "sticky": sticky,
            "session_id": body.session_id or unit_id,
            "run_id": getattr(result, "run_id", None),
            "content": getattr(result, "content", None),
        }
        return JSONResponse(payload)
