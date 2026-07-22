"""Assignment engine — turns (experiment, caller) into a concrete variant.

Ties the three planes together: the **registry** (what variants exist + baseline
weights), the **store** (live overrides + counters), and the pure **bucketing**
maths. The result is an ``Assignment`` the route uses to pick the agent, stamp the
trace, and tell the caller which arm it got.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .bucketing import choose, in_coverage
from .registry import Experiment
from .store import AllocationStore


@dataclass(frozen=True)
class Assignment:
    """The outcome of assigning a caller to an arm — everything the route needs."""

    experiment: str
    variant: str
    agent_id: str
    version: str
    unit_id: str
    sticky: bool  # False when we had to fall back to a random unit id
    reason: str  # "bucketed" | "forced" | "disabled" | "holdout"
    overridden: bool  # a runtime override was in effect


def resolve_unit_id(
    experiment: Experiment,
    user_id: str | None,
    session_id: str | None,
) -> tuple[str, bool]:
    """Pick the sticky bucketing id per the experiment's ``unit``.

    Returns ``(unit_id, sticky)``. ``sticky=False`` means we had no stable id and
    fell back to a random one — assignment still works, but that caller won't get
    the same arm next request. Prefer always passing a ``user_id``.
    """
    primary = user_id if experiment.unit == "user" else session_id
    fallback = session_id if experiment.unit == "user" else user_id
    unit_id = primary or fallback
    if unit_id:
        return unit_id, True
    return uuid.uuid4().hex, False


def assign_variant(
    experiment: Experiment,
    unit_id: str,
    store: AllocationStore,
    *,
    forced: str | None = None,
    record: bool = True,
) -> Assignment:
    """Assign ``unit_id`` to a variant of ``experiment``.

    Precedence: ``forced`` (explicit arm for testing) → disabled (control) →
    out-of-coverage holdout (control) → deterministic weighted bucketing.
    Set ``record=False`` for a dry-run preview that doesn't bump the monitor counts.
    """
    alloc = store.resolve(experiment)

    if forced is not None:
        variant = experiment.get(forced)  # KeyError → 404 at the route
        reason = "forced"
    elif not alloc.enabled:
        variant, reason = experiment.control, "disabled"
    elif not in_coverage(unit_id, experiment.key, alloc.coverage):
        variant, reason = experiment.control, "holdout"
    else:
        name = choose(unit_id, experiment.key, experiment.variant_names, alloc.weights)
        variant, reason = experiment.get(name), "bucketed"

    if record:
        store.record(experiment.key, variant.name)

    return Assignment(
        experiment=experiment.key,
        variant=variant.name,
        agent_id=variant.agent_id,
        version=variant.version,
        unit_id=unit_id,
        sticky=True,  # overwritten by the caller when resolve_unit_id fell back
        reason=reason,
        overridden=alloc.overridden,
    )
