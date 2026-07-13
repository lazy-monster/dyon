"""Shared in-memory fakes for framework-level unit tests.

None of these touch real infrastructure (Influx/Mongo/Redis/Neo4j). They
implement just enough of the storage protocols — including the async ``a*``
variants the layer loops await — to exercise the framework in isolation.
"""

from __future__ import annotations

import copy

import pytest


class FakeTimeSeriesStore:
    """In-memory TimeSeriesStore. ``values`` maps field -> latest value."""

    def __init__(self, values: dict | None = None) -> None:
        self.values: dict = dict(values or {})
        self.points: list[tuple[str, dict]] = []

    def set(self, field: str, value) -> None:
        self.values[field] = value

    # sync surface
    def get_latest(self, field: str, measurement: str = "asset_telemetry"):
        return self.values.get(field)

    def get_latest_fields(self, fields, measurement: str = "asset_telemetry"):
        return {f: self.values.get(f) for f in fields}

    def write_point(self, measurement: str, fields: dict, tags=None) -> None:
        self.points.append((measurement, dict(fields)))

    # async surface (awaited by the layer loops)
    async def aget_latest(self, field: str, measurement: str = "asset_telemetry"):
        return self.values.get(field)

    async def aget_latest_fields(self, fields, measurement: str = "asset_telemetry"):
        return {f: self.values.get(f) for f in fields}

    async def aquery_recent(self, field, minutes=10, measurement="asset_telemetry"):
        return None

    async def awrite_point(self, measurement: str, fields: dict, tags=None) -> None:
        self.points.append((measurement, dict(fields)))


class FakeCacheStore:
    def __init__(self) -> None:
        self.state = "unknown"
        self.kv: dict = {}

    def set_state(self, state: str) -> None:
        self.state = state

    def get_state(self) -> str:
        return self.state

    async def aset_state(self, state: str) -> None:
        self.state = state

    async def aget_state(self) -> str:
        return self.state

    async def aset_latest(self, field: str, value) -> None:
        self.kv[field] = value

    async def aget_latest_cached(self, field: str):
        return self.kv.get(field)


class FakeDocumentStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def log_event(self, event_type: str, payload: dict, severity: str = "info") -> None:
        self.events.append((event_type, payload, severity))

    async def alog_event(self, event_type: str, payload: dict, severity: str = "info") -> None:
        self.events.append((event_type, payload, severity))

    async def aget_recent_events(self, n: int = 20) -> list[dict]:
        return [e[1] for e in self.events[-n:]]


class FakeMongoCollection:
    """Minimal capped-collection stand-in: natural (insertion) order, $natural sort."""

    def __init__(self) -> None:
        self.docs: list[dict] = []

    def insert_one(self, body: dict) -> None:
        body["_id"] = len(self.docs)
        self.docs.append(copy.deepcopy(body))

    def find(self, query=None, projection=None):
        rows = []
        for d in self.docs:
            rows.append({k: v for k, v in d.items()
                         if not (projection and projection.get(k) == 0)})

        class _Cursor(list):
            def sort(self, *a, **k):
                return self

            def limit(self, n):
                return _Cursor(self[:n])

        return _Cursor(rows)

    def find_one(self, query=None, sort=None):
        if not self.docs:
            return None
        return copy.deepcopy(self.docs[-1])   # $natural -1 == last inserted


class FakeMongoDB:
    def __init__(self) -> None:
        self._col = FakeMongoCollection()

    def list_collection_names(self):
        return ["provenance_log"]

    def create_collection(self, *a, **k):
        return self._col

    def __getitem__(self, name):
        return self._col


class FakeMongoClient:
    def __init__(self) -> None:
        self._db = FakeMongoDB()

    def __getitem__(self, name):
        return self._db


@pytest.fixture
def fake_ts():
    return FakeTimeSeriesStore()


@pytest.fixture
def fake_cache():
    return FakeCacheStore()


@pytest.fixture
def fake_doc():
    return FakeDocumentStore()
