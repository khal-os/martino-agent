"""Experiment as a native AgentOS agent — A/B on ``POST /agents/{key}/runs``.

Registering an experiment as an Agno ``AgentFactory`` (a per-request agent
builder) makes it a **first-class agent**: Omni's native ``agno`` provider — and
any AgentOS client — can hit ``POST /agents/{experiment_key}/runs`` and get
sticky A/B routing with **all native features** (media/files, streaming,
sessions), because the run is handled by AgentOS exactly like any agent run. The
factory only decides *which* variant serves each request (bucketed by user).

This is the richest of the three A/B surfaces:
  - ``POST /experiments/{key}/run`` — explicit JSON control API (routes.py)
  - ``POST /omni/webhook``          — channel adapter, text-only (omni.py)
  - ``POST /agents/{key}/runs``     — native, full media + streaming (this module)

AgentOS deep-copies an agent per request anyway (request isolation), so building
a fresh variant here adds no cost beyond that. The factory forces the produced
agent's id to the experiment key, so sessions live under the experiment identity.
"""

from __future__ import annotations

from typing import Any

from ..agents import BUILDERS, get_agent
from ..config import Settings
from ..db import build_db
from ..observability import tag_experiment
from .engine import assign_variant, resolve_unit_id
from .registry import EXPERIMENTS, Experiment
from .store import AllocationStore, get_store


def _make_factory(experiment: Experiment, settings: Settings, store: AllocationStore) -> Any:
    from agno.agent.factory import AgentFactory

    def build_for_request(ctx: Any) -> Any:
        # ctx.user_id / ctx.session_id come from the run (form field or request.state).
        unit_id, _ = resolve_unit_id(experiment, ctx.user_id, ctx.session_id)
        assignment = assign_variant(experiment, unit_id, store)

        # Fresh copy so forcing the id (→ experiment key) never mutates the shared
        # cached variant agent. deep_copy() is what AgentOS does per request anyway.
        agent = get_agent(assignment.agent_id).deep_copy()
        # AgentOS enforces produced.id == factory.id; set it up front to avoid the
        # "component id must match the factory id" warning on every request.
        agent.id = experiment.key

        # The native path carries no ab_* run metadata, so stamp the arm directly
        # via a per-request pre-hook closed over the assignment.
        def tag(**_: Any) -> None:
            tag_experiment(experiment.key, assignment.variant, assignment.version)

        agent.pre_hooks = [*(agent.pre_hooks or []), tag]
        return agent

    return AgentFactory(
        id=experiment.key,
        db=build_db(settings),
        factory=build_for_request,
        name=f"Experiment · {experiment.key}",
        description=experiment.description or f"A/B experiment {experiment.key}",
    )


def build_experiment_factories(settings: Settings) -> list[Any]:
    """One ``AgentFactory`` per experiment, for serving A/B on ``/agents/{key}/runs``.

    Guards against a key colliding with a real agent id — that would make the
    ``/agents/{id}`` route ambiguous.
    """
    agent_ids = set(BUILDERS)
    collisions = agent_ids & set(EXPERIMENTS)
    if collisions:
        raise ValueError(
            f"Experiment keys collide with agent ids: {sorted(collisions)}. "
            f"Rename the experiment(s) — they share the /agents/{{id}} namespace."
        )
    store = get_store(settings.experiments_store_path)
    return [_make_factory(exp, settings, store) for exp in EXPERIMENTS.values()]
