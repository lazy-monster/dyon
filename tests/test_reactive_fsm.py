"""Tests for the reactive FSM engines.

Covers threshold-driven transitions, recovery, and the shutdown latch
(assessment §4.5), and in doing so exercises the async store path the engines
now use (assessment §3) against in-memory fakes.
"""

from __future__ import annotations

from conftest import FakeCacheStore, FakeDocumentStore, FakeTimeSeriesStore

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.events import EventBus
from dyon.reactive.fsm_engine import MultiStateFSMRuleEngine
from dyon.reactive.rule_engine import ThresholdRuleEngine


def _config():
    return TwinConfig(
        sensor_fields=[
            SensorFieldSpec(name="temp", warn_threshold=70.0,
                            crit_threshold=90.0, threshold_direction="high"),
        ]
    )


def _engine(ts):
    return ThresholdRuleEngine(
        _config(), EventBus(),
        ts_store=ts, cache=FakeCacheStore(), doc_store=FakeDocumentStore(),
    )


async def test_threshold_engine_warns_and_recovers():
    ts = FakeTimeSeriesStore({"temp": 50.0})
    eng = _engine(ts)

    assert await eng.evaluate() == "running"

    ts.set("temp", 75.0)          # above warn, below crit
    assert await eng.evaluate() == "warning"

    ts.set("temp", 50.0)          # back to nominal
    assert await eng.evaluate() == "running"


async def test_threshold_engine_shutdown_latches():
    ts = FakeTimeSeriesStore({"temp": 95.0})   # above crit
    eng = _engine(ts)

    assert await eng.evaluate() == "shutdown"

    # A single critical sample latches shutdown — returning to nominal must NOT
    # silently recover (there is no automatic restart trigger; §4.5).
    ts.set("temp", 50.0)
    assert await eng.evaluate() == "shutdown"
    ts.set("temp", 50.0)
    assert await eng.evaluate() == "shutdown"


async def test_acknowledge_and_restart_clears_latch():
    ts = FakeTimeSeriesStore({"temp": 95.0})
    eng = _engine(ts)
    assert await eng.evaluate() == "shutdown"

    # Operator-acknowledged recovery returns the FSM to running.
    ts.set("temp", 50.0)
    assert await eng.acknowledge_and_restart() == "running"
    # And it stays running on the next normal evaluation.
    assert await eng.evaluate() == "running"


async def test_acknowledge_is_noop_when_not_latched():
    ts = FakeTimeSeriesStore({"temp": 50.0})
    eng = _engine(ts)
    assert await eng.evaluate() == "running"
    assert await eng.acknowledge_and_restart() == "running"   # no-op


async def test_consecutive_crit_debounces_single_spike():
    cfg = _config()
    ts = FakeTimeSeriesStore({"temp": 95.0})     # critical
    eng = ThresholdRuleEngine(
        cfg, EventBus(),
        ts_store=ts, cache=FakeCacheStore(), doc_store=FakeDocumentStore(),
        consecutive_crit_to_latch=2,
    )
    # One critical sample is not enough to latch when 2 in a row are required.
    assert await eng.evaluate() == "running"
    # A transient recovery resets the streak…
    ts.set("temp", 50.0)
    assert await eng.evaluate() == "running"
    # …so two non-consecutive spikes still don't latch.
    ts.set("temp", 95.0)
    assert await eng.evaluate() == "running"
    # But two consecutive criticals do.
    assert await eng.evaluate() == "shutdown"


async def test_threshold_engine_writes_state_to_cache():
    ts = FakeTimeSeriesStore({"temp": 95.0})
    cache = FakeCacheStore()
    eng = ThresholdRuleEngine(
        _config(), EventBus(),
        ts_store=ts, cache=cache, doc_store=FakeDocumentStore(),
    )
    await eng.evaluate()
    assert cache.state == "shutdown"


# --- MultiStateFSMRuleEngine ------------------------------------------------

class _ThreeStateEngine(MultiStateFSMRuleEngine):
    _states = ["NOMINAL", "ALERT", "FAULT"]
    _transitions = []
    _initial_state = "NOMINAL"
    _severity_map = {"ALERT": "warning", "FAULT": "critical"}

    def compute_desired_state(self, readings):
        v = readings.get("level")
        if v is None:
            return None
        if v >= 90:
            return "FAULT"
        if v >= 70:
            return "ALERT"
        return "NOMINAL"


async def test_multistate_engine_transitions_across_states():
    ts = FakeTimeSeriesStore({"level": 10.0})
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="level")])
    eng = _ThreeStateEngine(
        cfg, EventBus(),
        ts_store=ts, cache=FakeCacheStore(), doc_store=FakeDocumentStore(),
    )

    assert await eng.evaluate() == "NOMINAL"
    ts.set("level", 75.0)
    assert await eng.evaluate() == "ALERT"
    ts.set("level", 95.0)
    assert await eng.evaluate() == "FAULT"
    ts.set("level", 10.0)         # this engine allows recovery via _goto_*
    assert await eng.evaluate() == "NOMINAL"
