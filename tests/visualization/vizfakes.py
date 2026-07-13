"""Fakes specific to the visualization API tests.

These extend the framework-level fakes with the query surface the viz endpoints
use (``aquery_recent_fields``) and bundle stores behind a stand-in ``data``
service so the registry-based store discovery in ``serve.py`` is exercised."""

from __future__ import annotations

import time

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.events import EventBus
from dyon.services.base import ServiceRegistry


class VizFakeTimeSeriesStore:
    """In-memory store with the query surface the viz endpoints call."""

    def __init__(self, latest: dict | None = None, history: dict | None = None):
        self._latest = dict(latest or {})
        self._history = dict(history or {})   # field -> [{"ts", "value"}]

    def query_recent(self, field, minutes=10, measurement="asset_telemetry"):
        # No DataFrame history in the fake → the forecaster treats it as
        # insufficient data and returns an empty forecast (a 200, not a crash).
        return None

    def query_recent_fields(self, fields, minutes=60, measurement="asset_telemetry"):
        now = time.time()
        out = {}
        for f in fields:
            out[f] = self._history.get(f, [{"ts": now, "value": self._latest.get(f, 0.0)}])
        return out

    async def aget_latest_fields(self, fields, measurement="asset_telemetry"):
        return {f: self._latest.get(f) for f in fields}

    async def aquery_recent_fields(self, fields, minutes=60, measurement="asset_telemetry"):
        now = time.time()
        out = {}
        for f in fields:
            out[f] = self._history.get(f, [{"ts": now, "value": self._latest.get(f, 0.0)}])
        return out


class FakeDataService:
    """Stand-in for the registered ``data`` service (TelemetryRouter), exposing
    the same ``ts``/``doc``/``bus`` attributes ``serve.py`` discovers."""

    service_name = "data"
    dependencies: list[str] = []

    def __init__(self, ts, bus):
        self.ts = ts
        self.doc = None
        self.bus = bus


def make_config():
    return TwinConfig(
        asset_id="pump1", asset_name="Pump One", asset_type="centrifugal_pump",
        sensor_fields=[
            SensorFieldSpec(name="temp", unit="C", nominal=25.0,
                            warn_threshold=70.0, crit_threshold=90.0),
            SensorFieldSpec(name="moisture", unit="%", nominal=40.0,
                            crit_threshold=10.0, threshold_direction="low"),
        ],
    )


def make_registry(ts=None, bus=None):
    bus = bus or EventBus()
    ts = ts if ts is not None else VizFakeTimeSeriesStore(
        latest={"temp": 80.0, "moisture": 5.0},
    )
    registry = ServiceRegistry()
    registry.register(FakeDataService(ts, bus))
    return registry, bus, ts
