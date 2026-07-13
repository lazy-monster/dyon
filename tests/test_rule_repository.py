"""Tests for the PersistedRule -> Rule factory and dynamic rule reload (§4.10).

These exercise the wiring that turns repository records into live rules and
hot-reloads them into the engine, using a fake repository (no PostgreSQL).
"""

from __future__ import annotations

import pytest
from conftest import FakeCacheStore, FakeDocumentStore, FakeTimeSeriesStore

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.events import EventBus
from dyon.reactive.rule_engine import ThresholdRuleEngine
from dyon.reactive.rule_repository import PersistedRule, rule_from_persisted


def test_factory_builds_high_threshold_rule():
    r = rule_from_persisted(PersistedRule(
        "hot", "threshold", {"field": "t", "direction": "high", "threshold": 80.0}, "critical"))
    assert r.evaluate({"t": 90.0}) == "critical"
    assert r.evaluate({"t": 10.0}) is None
    assert r.evaluate({"t": None}) is None


def test_factory_builds_low_threshold_rule():
    r = rule_from_persisted(PersistedRule(
        "dry", "threshold", {"field": "m", "direction": "low", "threshold": 0.05}, "warning"))
    assert r.evaluate({"m": 0.01}) == "warning"
    assert r.evaluate({"m": 0.10}) is None


def test_factory_uses_fn_registry_for_custom_condition():
    r = rule_from_persisted(
        PersistedRule("spike", "rate_spike", {}, "warning"),
        fn_registry={"rate_spike": lambda readings: readings.get("roc", 0) > 5},
    )
    assert r.evaluate({"roc": 9}) == "warning"
    assert r.evaluate({"roc": 1}) is None


def test_factory_rejects_unknown_condition():
    with pytest.raises(ValueError, match="unknown condition_type"):
        rule_from_persisted(PersistedRule("x", "mystery", {}, "warning"))


class _FakeRepo:
    def __init__(self, rules):
        self._rules = rules

    async def load_active_rules(self):
        return self._rules


async def test_engine_hot_loads_dynamic_rule():
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="temp")])   # no static threshold
    repo = _FakeRepo([PersistedRule(
        "hot", "threshold", {"field": "temp", "direction": "high", "threshold": 80.0},
        "critical")])
    ts = FakeTimeSeriesStore({"temp": 90.0})
    eng = ThresholdRuleEngine(
        cfg, EventBus(),
        ts_store=ts, cache=FakeCacheStore(), doc_store=FakeDocumentStore(),
        rule_repository=repo,
    )
    # No dynamic rules until the repository is read.
    assert await eng.evaluate() == "running"
    await eng.initialise()
    assert eng._dynamic_rules
    assert await eng.evaluate() == "shutdown"   # dynamic critical rule fires
