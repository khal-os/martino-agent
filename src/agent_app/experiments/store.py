"""Runtime allocation store — the knob you turn *without a redeploy*.

The registry (``registry.py``) holds each experiment's **baseline** weights in
git. This store holds the **live overrides** an operator sets at runtime via
``PUT /experiments/{id}/allocation`` — ramp B from 5% → 50%, pause an experiment,
shrink coverage — and the effective allocation is baseline ⊕ override.

## Persistence & scope (read this before shipping)
Overrides are persisted to a small JSON file (default ``tmp/experiment_allocations.json``).
That makes them survive restarts and be **shared across uvicorn workers on one
host** (each worker mtime-checks the file and reloads on change). It is *not*
shared across machines/pods. For multi-pod k8s, point ``resolve``/``set_allocation``
at a shared backend instead — the DB, Redis, or a real flag provider (LaunchDarkly
AI Configs / Statsig). This class is the seam: swap the load/save internals.

**Assignment counts** are in-process only (a cheap sanity gauge for the split);
they reset on restart and don't aggregate across workers/pods. The real,
cross-instance metrics — quality, latency, tokens, cost — live in LangWatch,
sliced by the ``ab.variant`` trace attribute. Don't treat these counts as the
measurement plane.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .bucketing import normalize_weights
from .registry import Experiment

# One experiment's override row: {"weights": {name: w}, "enabled": bool, "coverage": float}.
Override = dict[str, Any]


@dataclass(frozen=True)
class Allocation:
    """The *effective* allocation for an experiment (baseline ⊕ any override)."""

    weights: tuple[float, ...]  # normalized, aligned to experiment.variant_names
    enabled: bool
    coverage: float
    overridden: bool  # True if a runtime override is in effect (else pure baseline)


class AllocationStore:
    """Thread-safe, file-backed store of runtime weight/coverage/enabled overrides."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, Override] = {}
        self._mtime: float | None = None
        self._counts: Counter[tuple[str, str]] = Counter()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load_locked(self) -> dict[str, Override]:
        """Reload overrides from disk iff the file changed since we last read."""
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            self._cache, self._mtime = {}, None
            return self._cache
        if mtime != self._mtime:
            try:
                self._cache = json.loads(self._path.read_text(encoding="utf-8")) or {}
            except (json.JSONDecodeError, OSError):
                self._cache = {}
            self._mtime = mtime
        return self._cache

    def _save_locked(self, data: dict[str, Override]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)  # atomic swap
        self._mtime = self._path.stat().st_mtime
        self._cache = data

    # ── reads ────────────────────────────────────────────────────────────────
    def resolve(self, experiment: Experiment) -> Allocation:
        """Effective allocation = baseline from the registry ⊕ runtime override."""
        with self._lock:
            override = self._load_locked().get(experiment.key)
        names = experiment.variant_names
        if not override:
            return Allocation(
                weights=tuple(normalize_weights(experiment.baseline_weights)),
                enabled=experiment.enabled,
                coverage=experiment.coverage,
                overridden=False,
            )
        raw = override.get("weights")
        weights = [float(raw[n]) for n in names] if raw else list(experiment.baseline_weights)
        return Allocation(
            weights=tuple(normalize_weights(weights)),
            enabled=bool(override.get("enabled", experiment.enabled)),
            coverage=float(override.get("coverage", experiment.coverage)),
            overridden=True,
        )

    # ── writes ───────────────────────────────────────────────────────────────
    def set_allocation(
        self,
        experiment: Experiment,
        *,
        weights: dict[str, float] | None = None,
        enabled: bool | None = None,
        coverage: float | None = None,
    ) -> Allocation:
        """Apply a runtime override (validated against the experiment) and persist.

        ``weights`` must cover **exactly** the experiment's current variant names
        (no partial maps — that's how you avoid an ambiguous split). Values need
        not sum to 1; they're normalized on resolve.
        """
        if weights is not None:
            got, want = set(weights), set(experiment.variant_names)
            if got != want:
                raise ValueError(f"weights must cover exactly {sorted(want)}, got {sorted(got)}")
            if any(w < 0 for w in weights.values()):
                raise ValueError("weights must be non-negative")
            if sum(weights.values()) <= 0:
                raise ValueError("weights must sum to a positive number")
        if coverage is not None and not 0.0 <= coverage <= 1.0:
            raise ValueError("coverage must be in [0, 1]")

        with self._lock:
            data = dict(self._load_locked())
            current = dict(data.get(experiment.key, {}))
            if weights is not None:
                current["weights"] = {n: float(weights[n]) for n in experiment.variant_names}
            if enabled is not None:
                current["enabled"] = bool(enabled)
            if coverage is not None:
                current["coverage"] = float(coverage)
            data[experiment.key] = current
            self._save_locked(data)
        return self.resolve(experiment)

    def clear(self, experiment: Experiment) -> None:
        """Drop the override for an experiment → revert to the git baseline."""
        with self._lock:
            data = dict(self._load_locked())
            if data.pop(experiment.key, None) is not None:
                self._save_locked(data)

    # ── monitoring counters (in-process only) ────────────────────────────────
    def record(self, experiment_key: str, variant_name: str) -> None:
        with self._lock:
            self._counts[(experiment_key, variant_name)] += 1

    def counts(self, experiment_key: str) -> dict[str, int]:
        with self._lock:
            return {
                variant: n
                for (key, variant), n in self._counts.items()
                if key == experiment_key
            }


@lru_cache
def get_store(path: str) -> AllocationStore:
    """Process-cached store for ``path`` (call ``get_store.cache_clear()`` in tests)."""
    return AllocationStore(path)
