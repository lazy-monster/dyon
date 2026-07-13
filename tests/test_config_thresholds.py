"""Single-bound threshold handling (assessment §4.1).

A field configured with only a warn level (or only a crit level) must still be
honoured, not silently dropped, and consumers must skip the missing check
rather than raising.
"""

from __future__ import annotations

from conftest import FakeCacheStore, FakeDocumentStore, FakeTimeSeriesStore

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.events import EventBus
from dyon.reactive.rule_engine import ThresholdRuleEngine


def test_warn_only_field_is_included():
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="t", warn_threshold=70.0)])
    t = cfg.thresholds["t"]
    assert t["warn"] == 70.0
    assert t["crit"] is None


def test_crit_only_field_is_included():
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="t", crit_threshold=90.0)])
    t = cfg.thresholds["t"]
    assert t["crit"] == 90.0
    assert t["warn"] is None


def test_no_bounds_field_is_excluded():
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="t", nominal=1.0)])
    assert "t" not in cfg.thresholds


def _engine(cfg, ts):
    return ThresholdRuleEngine(
        cfg, EventBus(),
        ts_store=ts, cache=FakeCacheStore(), doc_store=FakeDocumentStore(),
    )


async def test_warn_only_drives_warning_but_never_shutdown():
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="t", warn_threshold=70.0)])
    ts = FakeTimeSeriesStore({"t": 95.0})       # far above warn, no crit configured
    eng = _engine(cfg, ts)
    assert await eng.evaluate() == "warning"    # not shutdown — no crit bound


async def test_crit_only_drives_shutdown():
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="t", crit_threshold=90.0)])
    ts = FakeTimeSeriesStore({"t": 95.0})
    eng = _engine(cfg, ts)
    assert await eng.evaluate() == "shutdown"
