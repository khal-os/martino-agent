"""A/B experiments — bucketing maths, registry, store, engine and HTTP surface.

All offline: no model calls. The one path we don't drive here is the *happy*
POST /experiments/{id}/run (it would hit the real model); its routing is proven
via the engine tests + the error paths below, and end-to-end via evals/cases.py.
"""

from __future__ import annotations

import importlib
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from agent_app import config
from agent_app.experiments import bucketing
from agent_app.experiments.engine import assign_variant, resolve_unit_id
from agent_app.experiments.registry import Experiment, Variant, get_experiment
from agent_app.experiments.store import AllocationStore

VARIANTS = ("A", "B")


def _exp(**kw) -> Experiment:
    base = {
        "key": "t",
        "variants": (Variant("A", "agent-a"), Variant("B", "agent-b")),
    }
    base.update(kw)
    return Experiment(**base)  # type: ignore[arg-type]


# ── bucketing ────────────────────────────────────────────────────────────────


def test_bucket_is_deterministic_and_in_range():
    for i in range(1000):
        b = bucketing.bucket(f"u{i}", "salt")
        assert 0.0 <= b < 1.0
        assert b == bucketing.bucket(f"u{i}", "salt")  # stable


def test_choose_is_sticky_for_same_unit():
    for i in range(500):
        uid = f"user-{i}"
        first = bucketing.choose(uid, "exp", VARIANTS, [0.5, 0.5])
        assert all(bucketing.choose(uid, "exp", VARIANTS, [0.5, 0.5]) == first for _ in range(3))


def test_distribution_tracks_weights():
    counts = Counter(bucketing.choose(f"u{i}", "exp", VARIANTS, [0.2, 0.8]) for i in range(20000))
    share_b = counts["B"] / 20000
    assert 0.77 < share_b < 0.83  # ~0.80 with sampling slack


def test_salt_reshuffles_independently():
    # A different salt (experiment key) must not reproduce the same split assignment.
    a = [bucketing.choose(f"u{i}", "exp-1", VARIANTS, [0.5, 0.5]) for i in range(2000)]
    b = [bucketing.choose(f"u{i}", "exp-2", VARIANTS, [0.5, 0.5]) for i in range(2000)]
    agree = sum(x == y for x, y in zip(a, b, strict=True)) / 2000
    assert 0.4 < agree < 0.6  # ~independent, not correlated


def test_choose_supports_n_way():
    variants = ("A", "B", "C", "D")
    counts = Counter(
        bucketing.choose(f"u{i}", "e", variants, [0.25, 0.25, 0.25, 0.25]) for i in range(8000)
    )
    assert set(counts) == set(variants)
    assert all(1600 < n < 2400 for n in counts.values())


def test_coverage_gate_fraction():
    n = sum(bucketing.in_coverage(f"u{i}", "e", 0.1) for i in range(10000))
    assert 850 < n < 1150  # ~10%
    assert bucketing.in_coverage("x", "e", 1.0) is True
    assert bucketing.in_coverage("x", "e", 0.0) is False


def test_normalize_weights_validation():
    assert bucketing.normalize_weights([1, 3]) == [0.25, 0.75]
    for bad in ([], [-1, 2], [0, 0]):
        with pytest.raises(ValueError):
            bucketing.normalize_weights(bad)


# ── native agent factory ─────────────────────────────────────────────────────


def test_experiment_registered_as_native_agent_factory():
    """Each experiment becomes an AgentFactory served at /agents/{key}/runs."""
    from agent_app.config import get_settings
    from agent_app.experiments import build_experiment_factories

    get_settings.cache_clear()
    factories = build_experiment_factories(get_settings())
    assert "assistant-tone" in {f.id for f in factories}


def test_factory_guards_key_collision_with_agent_id(monkeypatch):
    """An experiment key equal to a real agent id would make /agents/{id} ambiguous."""
    from agent_app.config import get_settings
    from agent_app.experiments import factory as fac
    from agent_app.experiments.registry import Experiment, Variant

    clash = Experiment(
        key="assistant",  # collides with the registered 'assistant' agent
        variants=(Variant("A", "assistant"), Variant("B", "assistant-concise")),
    )
    monkeypatch.setitem(fac.EXPERIMENTS, "assistant", clash)
    with pytest.raises(ValueError, match="collide"):
        fac.build_experiment_factories(get_settings())


# ── registry ─────────────────────────────────────────────────────────────────


def test_registry_has_example_experiment():
    exp = get_experiment("assistant-tone")
    assert exp.variant_names == ("A", "B")
    assert exp.get("B").agent_id == "assistant-concise"
    assert exp.control.name == "A"


def test_get_experiment_unknown_raises():
    with pytest.raises(KeyError):
        get_experiment("nope")


def test_experiment_validation():
    with pytest.raises(ValueError):  # < 2 variants
        Experiment(key="x", variants=(Variant("A", "a"),))
    with pytest.raises(ValueError):  # duplicate names
        Experiment(key="x", variants=(Variant("A", "a"), Variant("A", "b")))
    with pytest.raises(ValueError):  # bad coverage
        _exp(coverage=1.5)
    with pytest.raises(ValueError):  # bad unit
        _exp(unit="tenant")


# ── store ────────────────────────────────────────────────────────────────────


def test_store_baseline_when_no_override(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    alloc = store.resolve(_exp())
    assert alloc.weights == (0.5, 0.5)
    assert alloc.overridden is False


def test_store_override_weights_and_persist(tmp_path):
    path = tmp_path / "a.json"
    exp = _exp()
    AllocationStore(path).set_allocation(exp, weights={"A": 0.1, "B": 0.9})
    # A fresh store instance reads the persisted override from disk.
    alloc = AllocationStore(path).resolve(exp)
    assert alloc.weights == (0.1, 0.9)
    assert alloc.overridden is True


def test_store_enabled_and_coverage_override(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    exp = _exp()
    store.set_allocation(exp, enabled=False, coverage=0.25)
    alloc = store.resolve(exp)
    assert alloc.enabled is False
    assert alloc.coverage == 0.25


def test_store_rejects_bad_weights(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    exp = _exp()
    with pytest.raises(ValueError):  # missing a variant
        store.set_allocation(exp, weights={"A": 1.0})
    with pytest.raises(ValueError):  # unknown variant
        store.set_allocation(exp, weights={"A": 0.5, "B": 0.5, "C": 0.1})
    with pytest.raises(ValueError):  # negative
        store.set_allocation(exp, weights={"A": -1.0, "B": 1.0})


def test_store_clear_reverts_to_baseline(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    exp = _exp()
    store.set_allocation(exp, weights={"A": 0.0, "B": 1.0})
    store.clear(exp)
    assert store.resolve(exp).overridden is False


def test_store_counts(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    store.record("t", "A")
    store.record("t", "A")
    store.record("t", "B")
    assert store.counts("t") == {"A": 2, "B": 1}


# ── engine ───────────────────────────────────────────────────────────────────


def test_assign_baseline_is_sticky_and_records(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    exp = _exp()
    a1 = assign_variant(exp, "alice", store)
    a2 = assign_variant(exp, "alice", store)
    assert a1.variant == a2.variant
    assert a1.reason == "bucketed"
    assert store.counts("t")[a1.variant] == 2


def test_assign_forced(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    asg = assign_variant(_exp(), "alice", store, forced="B")
    assert asg.variant == "B" and asg.reason == "forced"


def test_assign_disabled_returns_control(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    exp = _exp()
    store.set_allocation(exp, enabled=False)
    asg = assign_variant(exp, "alice", store)
    assert asg.variant == "A" and asg.reason == "disabled"


def test_assign_holdout_returns_control(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    exp = _exp(coverage=0.0)  # nobody eligible → everyone gets control
    asg = assign_variant(exp, "alice", store)
    assert asg.variant == "A" and asg.reason == "holdout"


def test_assign_record_false_is_dry_run(tmp_path):
    store = AllocationStore(tmp_path / "a.json")
    assign_variant(_exp(), "alice", store, record=False)
    assert store.counts("t") == {}


def test_resolve_unit_id_precedence():
    exp_user = _exp(unit="user")
    assert resolve_unit_id(exp_user, "u1", "s1") == ("u1", True)
    assert resolve_unit_id(exp_user, None, "s1") == ("s1", True)  # fall back to session
    exp_sess = _exp(unit="session")
    assert resolve_unit_id(exp_sess, "u1", "s1") == ("s1", True)
    uid, sticky = resolve_unit_id(exp_user, None, None)  # no stable id → random
    assert sticky is False and len(uid) == 32


# ── trace tagging (telemetry contract, no network) ───────────────────────────


class _FakeSpan:
    def __init__(self):
        self.attrs: dict[str, object] = {}

    def is_recording(self):
        return True

    def set_attribute(self, key, value):
        self.attrs[key] = value


def test_tag_experiment_stamps_span(monkeypatch):
    from opentelemetry import trace as otel

    from agent_app import observability

    span = _FakeSpan()
    monkeypatch.setattr(otel, "get_current_span", lambda: span)
    observability.tag_experiment("assistant-tone", "B", "2026-07-16")
    assert span.attrs == {
        "ab.experiment": "assistant-tone",
        "ab.variant": "B",
        "ab.variant_version": "2026-07-16",
    }


def test_pre_hook_tags_from_run_metadata(monkeypatch):
    from opentelemetry import trace as otel

    from agent_app.hooks import tag_experiment

    span = _FakeSpan()
    monkeypatch.setattr(otel, "get_current_span", lambda: span)
    # The run endpoint passes the assignment as run metadata; the hook reads it.
    tag_experiment(metadata={"ab_experiment": "e1", "ab_variant": "A", "ab_variant_version": "3"})
    assert span.attrs["ab.variant"] == "A"
    assert span.attrs["ab.experiment"] == "e1"

    # Ordinary runs carry no ab_* metadata → no-op (no tagging, no crash).
    span2 = _FakeSpan()
    monkeypatch.setattr(otel, "get_current_span", lambda: span2)
    tag_experiment(metadata={"turn": 1})
    assert span2.attrs == {}


# ── HTTP surface ─────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("CONNECTOR_CATALOG_URL", raising=False)
    monkeypatch.delenv("CONNECTOR_REGISTER_URL", raising=False)
    monkeypatch.delenv("TRACES_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("EXPERIMENTS_STORE_PATH", str(tmp_path / "alloc.json"))
    config.get_settings.cache_clear()
    from agent_app.experiments import store as store_mod

    store_mod.get_store.cache_clear()
    import agent_app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def test_list_experiments(client):
    r = client.get("/experiments")
    assert r.status_code == 200
    keys = [e["key"] for e in r.json()["experiments"]]
    assert "assistant-tone" in keys


def test_get_experiment_monitor(client):
    r = client.get("/experiments/assistant-tone")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "baseline"
    assert {v["name"] for v in body["variants"]} == {"A", "B"}


def test_get_unknown_experiment_404(client):
    assert client.get("/experiments/nope").status_code == 404


def test_put_allocation_updates_split(client):
    r = client.put("/experiments/assistant-tone/allocation", json={"weights": {"A": 0.1, "B": 0.9}})
    assert r.status_code == 200
    weights = {v["name"]: v["weight"] for v in r.json()["variants"]}
    assert weights == {"A": 0.1, "B": 0.9}
    assert r.json()["source"] == "override"


def test_put_allocation_rejects_bad_weights(client):
    r = client.put("/experiments/assistant-tone/allocation", json={"weights": {"A": 1.0}})
    assert r.status_code == 422


def test_assign_dry_run_does_not_count(client):
    r = client.post("/experiments/assistant-tone/assign", json={"user_id": "alice"})
    assert r.status_code == 200
    assert r.json()["variant"] in ("A", "B")
    # dry-run must not bump the monitor counters
    assert client.get("/experiments/assistant-tone").json()["total_assignments"] == 0


def test_run_dispatches_and_passes_ab_metadata(client, monkeypatch):
    """Happy path without a model call: stub the agent, assert the route dispatches
    to the assigned arm, forwards ab_* metadata (which the pre-hook tags), and
    returns the arm + content."""
    captured = {}

    class _Result:
        content = "stubbed answer"
        run_id = "run-1"

    class _Agent:
        async def arun(self, **kwargs):
            captured.update(kwargs)
            return _Result()

    import agent_app.experiments.routes as routes_mod

    monkeypatch.setattr(routes_mod, "get_agent", lambda _id: _Agent())

    r = client.post(
        "/experiments/assistant-tone/run",
        json={"message": "how much is milk?", "user_id": "user-123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["variant"] in ("A", "B")
    assert body["content"] == "stubbed answer"
    assert body["run_id"] == "run-1"
    # the assignment must travel as run metadata (the pre-hook reads ab_variant)
    assert captured["metadata"]["ab_variant"] == body["variant"]
    assert captured["metadata"]["ab_experiment"] == "assistant-tone"
    assert captured["session_id"] == "user-123"


def test_run_unknown_experiment_404(client):
    r = client.post("/experiments/nope/run", json={"message": "hi", "user_id": "u"})
    assert r.status_code == 404


def test_run_forced_unknown_variant_422(client):
    r = client.post(
        "/experiments/assistant-tone/run",
        json={"message": "hi", "user_id": "u", "variant": "Z"},
    )
    assert r.status_code == 422


@pytest.fixture(autouse=True)
def _restore_main():
    """Leave agent_app.main in its default state after reloads (mirrors test_api)."""
    import os

    yield
    os.environ.pop("CONNECTOR_CATALOG_URL", None)
    os.environ.pop("CONNECTOR_REGISTER_URL", None)
    os.environ.pop("TRACES_OTLP_ENDPOINT", None)
    config.get_settings.cache_clear()
    import agent_app.main as main

    importlib.reload(main)
