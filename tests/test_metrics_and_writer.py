"""Data-path observability: counters and TelemetryRouter drain hygiene.

The design keeps ingest alive across a storage outage; the hardening adds
visibility — a named counter registry the /health endpoint dumps, and a
TelemetryRouter that drains its queue on shutdown without letting one poison item
discard the rest.
"""

from __future__ import annotations

import pytest

from dyon.core import metrics
from dyon.core.config import TwinConfig
from dyon.core.events import EventBus
from dyon.data.writer import TelemetryRouter


def _config():
    return TwinConfig(sensor_fields=[{"name": "temp"}])


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def test_counter_registry_round_trip():
    metrics.increment("x")
    metrics.increment("x", 4)
    metrics.increment("y")
    snap = metrics.snapshot()
    assert snap == {"x": 5, "y": 1}
    # snapshot is a copy — mutating it must not affect the registry
    snap["x"] = 999
    assert metrics.snapshot()["x"] == 5


class _OK:
    async def aset_latest(self, *a, **k):
        pass

    async def awrite_point(self, *a, **k):
        pass

    async def aget_latest_fields(self, fields, measurement="asset_telemetry"):
        return {}

    async def alog_event(self, *a, **k):
        pass


async def test_drain_survives_a_poison_item():
    config = _config()
    field = config.field_names[0]
    processed: list = []

    class _CountingRouter(TelemetryRouter):
        async def _process(self, data):
            if data.get("poison"):
                raise ValueError("bad item")
            processed.append(data)

    r = _CountingRouter(
        config, EventBus(), ts_store=_OK(), doc_store=_OK(), cache=_OK(),
    )
    await r._queue.put({field: 1.0})
    await r._queue.put({"poison": True})
    await r._queue.put({field: 2.0})
    await r.stop()
    # Items 1 and 3 processed; the poison item counted as dropped, not silently lost.
    assert {field: 1.0} in processed and {field: 2.0} in processed
    assert metrics.snapshot().get("telemetry.dropped") == 1


async def test_route_processes_a_clean_item_end_to_end():
    config = _config()
    field = config.field_names[0]
    r = TelemetryRouter(config, EventBus(), ts_store=_OK(), doc_store=_OK(), cache=_OK())
    await r.route({field: 42.0})
    assert r._queue.qsize() == 1
    await r.stop()          # drains the one queued item
    assert r._queue.empty()
    assert metrics.snapshot().get("telemetry.dropped") is None   # nothing dropped
