"""Deterministic, sticky bucketing — the maths behind A/B/C/D/E assignment.

This is the *routing plane*. Given a stable unit id (a user, or a session) it
returns the same variant forever, so a user never flips arms mid-conversation —
the property that makes behavioural metrics interpretable. It's pure, has no
dependencies, and is trivially unit-testable (see tests/test_experiments.py).

## Why hashing (and not ``random``)
``random`` is per-call: the same user would get a different arm every request,
contaminating within-user metrics. Feature-flag systems (Unleash, GrowthBook,
Statsig) instead **hash a stable id + a per-experiment salt** into a bucket and
map buckets to variants by weight. Same id + same salt → same bucket → same arm.

## Which algorithm
We copy **GrowthBook's model**: FNV-1a 32-bit, "hash version 2" (double-hash).
Reasons: (1) FNV-1a is ~5 lines with no dependency; (2) it yields a clean float
in ``[0, 1)``; (3) the per-experiment salt means two concurrent experiments
reshuffle the same users independently (no correlation bias — the exact flaw
GrowthBook's v2 fixed). Unleash uses MurmurHash3 and Statsig uses SHA-256; both
work, both are more code. See docs/ab-testing.md for the comparison.

Refs:
- GrowthBook "build your own SDK" (FNV-1a, hashVersion 2):
  https://docs.growthbook.io/lib/build-your-own
- Unleash stickiness (MurmurHash3):  https://docs.getunleash.io/concepts/stickiness
"""

from __future__ import annotations

from collections.abc import Sequence

_FNV_OFFSET_BASIS_32 = 0x811C9DC5
_FNV_PRIME_32 = 0x01000193
_UINT32_MASK = 0xFFFFFFFF


def fnv1a_32(text: str) -> int:
    """32-bit FNV-1a hash of ``text`` (deterministic, non-cryptographic)."""
    h = _FNV_OFFSET_BASIS_32
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * _FNV_PRIME_32) & _UINT32_MASK
    return h


def bucket(unit_id: str, salt: str) -> float:
    """Map ``unit_id`` to a stable float in ``[0, 1)``, reshuffled per ``salt``.

    ``salt`` is the per-experiment seed: the same user lands in an independent
    bucket for each experiment, so parallel experiments don't correlate. The
    double-hash (GrowthBook hashVersion 2) removes a subtle bias present when the
    id and salt are simply concatenated once.
    """
    n = fnv1a_32(str(fnv1a_32(salt + unit_id)))
    return (n % 10000) / 10000.0


def normalize_weights(weights: Sequence[float]) -> list[float]:
    """Scale weights so they sum to 1.0. Rejects empty/negative/all-zero input."""
    if not weights:
        raise ValueError("weights must be non-empty")
    if any(w < 0 for w in weights):
        raise ValueError(f"weights must be non-negative, got {list(weights)}")
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    return [w / total for w in weights]


def choose(
    unit_id: str,
    salt: str,
    variants: Sequence[str],
    weights: Sequence[float],
) -> str:
    """Return the variant ``unit_id`` is assigned to (sticky, weighted, N-way).

    ``variants`` and ``weights`` are parallel sequences; weights are normalized
    so they need not sum to 1. Works for A/B and any number of arms (C/D/E…).

    Monotonic-ramp note: raising one arm's weight only pulls *additional* users
    into it from adjacent buckets; already-assigned users keep their arm as long
    as you grow a weight at the boundary rather than reordering the variants.
    """
    if len(variants) != len(weights):
        raise ValueError(f"variants ({len(variants)}) and weights ({len(weights)}) must align")
    point = bucket(unit_id, salt)
    cumulative = 0.0
    for variant, weight in zip(variants, normalize_weights(weights), strict=True):
        cumulative += weight
        if point < cumulative:
            return variant
    return variants[-1]  # float-rounding safety net


def in_coverage(unit_id: str, salt: str, coverage: float) -> bool:
    """True if ``unit_id`` falls inside a partial rollout / holdout ``coverage``.

    Uses an **independent** gate hash (``salt + ':gate'``) so the coverage
    decision doesn't correlate with the variant split. ``coverage=0.05`` = a 5%
    canary; the other 95% should be served the control arm by the caller.
    """
    if coverage >= 1.0:
        return True
    if coverage <= 0.0:
        return False
    return bucket(unit_id, salt + ":gate") < coverage
