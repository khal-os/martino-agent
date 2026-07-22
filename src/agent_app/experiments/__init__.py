"""A/B (and C/D/E…) experiments for agents — sticky routing + LangWatch metrics.

A production-ready pattern layered on AgentOS, distilled from how feature-flag
platforms (GrowthBook, Unleash, Statsig) route traffic and how LLM-observability
platforms (LangWatch) measure it. Neither Agno nor LangWatch ships an online A/B
primitive, so this supplies the routing plane and hands measurement to LangWatch.

Four planes, one per module:
  * ``bucketing`` — pure deterministic, sticky assignment maths (no deps).
  * ``registry``  — the git-versioned "folder of variants" (baseline weights).
  * ``store``     — live, remotely-tunable weights + monitor counters.
  * ``engine``    — assign_variant(): registry ⊕ store ⊕ bucketing → an Assignment.
  * ``routes``    — the run / control / monitor HTTP endpoints.

See docs/ab-testing.md for the full design, market comparison and citations.
"""

from __future__ import annotations

from .engine import Assignment, assign_variant, resolve_unit_id
from .factory import build_experiment_factories
from .registry import EXPERIMENTS, Experiment, Variant, get_experiment
from .routes import register_experiment_routes
from .store import AllocationStore, get_store

__all__ = [
    "EXPERIMENTS",
    "AllocationStore",
    "Assignment",
    "Experiment",
    "Variant",
    "assign_variant",
    "build_experiment_factories",
    "get_experiment",
    "get_store",
    "register_experiment_routes",
    "resolve_unit_id",
]
