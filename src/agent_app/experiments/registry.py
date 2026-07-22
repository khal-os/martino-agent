"""Experiment registry — the versioned "folder of variants".

An **experiment** is a named traffic split over two or more **variants**, where
each variant points at a registered Agno agent (by ``agent_id``). This module is
the single, git-versioned source of truth for *what* experiments exist and their
*baseline* weights. The live, remotely-tunable weights layer on top (see
``store.py``); assignment ties the two together (see ``engine.py``).

Design (mirrors ``models.PROVIDERS`` / ``agents.BUILDERS``): variants are plain
frozen data, registered in ``EXPERIMENTS``. Adding an experiment = add an
``Experiment`` here; adding a variant = register its agent in
``agents/__init__.py`` and add a ``Variant`` row. Git history *is* the version
log; ``Variant.version`` is a human-facing pin stamped on every trace so you can
attribute an outcome to the exact arm that served it.

Why variant = a whole registered Agent (not a mutated one): it's the pattern the
Agno docs steer toward — distinct ``Agent`` instances with stable ids sharing one
db/session — and it lets you also hit each arm directly at
``/agents/{agent_id}/runs`` for debugging. A variant can differ from another by
prompt, model, tools, or hooks — anything the builder varies.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    """One arm of an experiment → a registered agent, with a baseline weight."""

    name: str  # arm label shown in traces / the UI: "A", "B", "concise", …
    agent_id: str  # id of a registered agent (resolved via agents.get_agent)
    weight: float = 1.0  # baseline traffic share (normalized; runtime-overridable)
    version: str = "1"  # human-facing pin, stamped on the trace as ab.variant_version
    description: str = ""


@dataclass(frozen=True)
class Experiment:
    """A named, sticky traffic split over ``variants``.

    ``key`` doubles as the hashing salt, so renaming it re-randomizes every user's
    assignment — treat it as immutable once live. ``unit`` selects the sticky
    dimension: ``"user"`` (default) buckets by ``user_id``; ``"session"`` buckets
    by ``session_id`` (use when you have no stable user identity).
    """

    key: str  # stable id + hashing salt; the {experiment_id} in the URL
    variants: tuple[Variant, ...]
    description: str = ""
    unit: str = "user"  # "user" | "session" — the sticky bucketing dimension
    coverage: float = 1.0  # partial rollout: fraction eligible; rest get control
    enabled: bool = True  # off → everyone gets the control arm

    def __post_init__(self) -> None:
        if len(self.variants) < 2:
            raise ValueError(f"experiment {self.key!r} needs >= 2 variants")
        names = [v.name for v in self.variants]
        if len(set(names)) != len(names):
            raise ValueError(f"experiment {self.key!r} has duplicate variant names: {names}")
        if not 0.0 <= self.coverage <= 1.0:
            raise ValueError(f"experiment {self.key!r} coverage must be in [0, 1]")
        if self.unit not in ("user", "session"):
            raise ValueError(f"experiment {self.key!r} unit must be 'user' or 'session'")

    @property
    def variant_names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variants)

    @property
    def baseline_weights(self) -> tuple[float, ...]:
        return tuple(v.weight for v in self.variants)

    @property
    def control(self) -> Variant:
        """The fallback arm (first listed): served when disabled or out of coverage."""
        return self.variants[0]

    def get(self, name: str) -> Variant:
        for variant in self.variants:
            if variant.name == name:
                return variant
        raise KeyError(f"experiment {self.key!r} has no variant {name!r} (have: {self.variant_names})")


# ─── The registry ────────────────────────────────────────────────────────────
# key → Experiment. Keep it small; split into experiments/<name>.py modules if it
# grows (same move as agents/). Each variant's agent_id must be registered in
# agents/__init__.py:BUILDERS or the run endpoint 404s at request time.
EXPERIMENTS: dict[str, Experiment] = {
    # Example: does a terse persona win on user satisfaction without costing
    # quality? A (detailed) vs B (concise) — same tools, db and hooks, different
    # system prompt. Primary metric: 👍 rate (POST /feedback). Guardrails:
    # tokens/latency/cost — all sliced by ab.variant in LangWatch.
    "assistant-tone": Experiment(
        key="assistant-tone",
        description="Detailed vs concise assistant persona (prompt A/B).",
        unit="user",
        variants=(
            Variant(
                name="A",
                agent_id="assistant",
                weight=0.5,
                version="2026-07-16",
                description="Control — the default, detailed assistant.",
            ),
            Variant(
                name="B",
                agent_id="assistant-concise",
                weight=0.5,
                version="2026-07-16",
                description="Challenger — a terser, fewer-tokens persona.",
            ),
        ),
    ),
}


def get_experiment(key: str) -> Experiment:
    """Look up an experiment by key, failing with the list of known keys."""
    experiment = EXPERIMENTS.get(key)
    if experiment is None:
        raise KeyError(f"No experiment {key!r}. Registered: {sorted(EXPERIMENTS)}")
    return experiment
