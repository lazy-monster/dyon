"""The in-process stores must behave like the networked ones they stand in for.

These are the adapters a demo, a test, or an edge node runs on when there is no
Influx/Mongo/Redis/MinIO to talk to, so the contract that matters is the storage
protocol itself: windowed time-series queries, ordered and filterable events,
cache keys that expire, and the redis-shaped client surface that
``SessionStore`` reaches for.
"""

from __future__ import annotations

import os
import time

import pytest

from dyon.core.config import TwinConfig
from dyon.data import (
    FileBackedObjectAdapter,
    InMemoryCacheAdapter,
    InMemoryDocumentAdapter,
    InMemoryObjectAdapter,
    InMemoryTimeSeriesAdapter,
)
from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore
from dyon.session.context import SessionContext, SessionStore


@pytest.fixture
def config() -> TwinConfig:
    return TwinConfig(asset_id="unit_001", asset_type="unit", asset_name="Unit")


# --------------------------------------------------------------------------- #
# Time series
# --------------------------------------------------------------------------- #


def test_timeseries_satisfies_protocol(config):
    assert isinstance(InMemoryTimeSeriesAdapter(config), TimeSeriesStore)


def test_latest_reading_wins(config):
    ts = InMemoryTimeSeriesAdapter(config)
    ts.write_point("asset_telemetry", {"temp_c": 20.0})
    ts.write_point("asset_telemetry", {"temp_c": 22.5})
    assert ts.get_latest("temp_c") == 22.5
    assert ts.get_latest("missing") is None


def test_query_window_excludes_older_points(config):
    ts = InMemoryTimeSeriesAdapter(config)
    now = time.time()
    ts.write_point("asset_telemetry", {"temp_c": 1.0}, timestamp=now - 3600)
    ts.write_point("asset_telemetry", {"temp_c": 2.0}, timestamp=now - 60)

    recent = ts.query_recent_fields(["temp_c"], minutes=10)["temp_c"]
    assert [row["value"] for row in recent] == [2.0]

    everything = ts.query_recent_fields(["temp_c"], minutes=120)["temp_c"]
    assert [row["value"] for row in everything] == [1.0, 2.0]


def test_query_recent_returns_influx_shaped_frame(config):
    # The forecaster renames _time/_value straight off this frame.
    ts = InMemoryTimeSeriesAdapter(config)
    ts.write_point("asset_telemetry", {"temp_c": 5.0})
    frame = ts.query_recent("temp_c", minutes=10)
    assert list(frame.columns) == ["_time", "_value", "_field"]
    assert frame["_value"].tolist() == [5.0]

    empty = ts.query_recent("nothing_here", minutes=10)
    assert empty.empty
    assert list(empty.columns) == ["_time", "_value", "_field"]


def test_measurements_are_separate_series(config):
    ts = InMemoryTimeSeriesAdapter(config)
    ts.write_point("asset_telemetry", {"temp_c": 1.0})
    ts.write_point("model_output", {"temp_c": 9.0})
    assert ts.get_latest("temp_c", "asset_telemetry") == 1.0
    assert ts.get_latest("temp_c", "model_output") == 9.0


def test_series_is_bounded(config):
    ts = InMemoryTimeSeriesAdapter(config, max_points=10)
    for i in range(50):
        ts.write_point("asset_telemetry", {"temp_c": float(i)})
    rows = ts.query_recent_fields(["temp_c"], minutes=60)["temp_c"]
    assert len(rows) == 10
    assert rows[-1]["value"] == 49.0


def test_non_numeric_reading_is_dropped_not_raised(config):
    ts = InMemoryTimeSeriesAdapter(config)
    ts.write_point("asset_telemetry", {"state": "running", "temp_c": 3.0})
    assert ts.get_latest("state") is None
    assert ts.get_latest("temp_c") == 3.0


@pytest.mark.asyncio
async def test_timeseries_async_surface(config):
    ts = InMemoryTimeSeriesAdapter(config)
    await ts.awrite_point("asset_telemetry", {"temp_c": 7.0})
    assert await ts.aget_latest("temp_c") == 7.0
    assert await ts.aget_latest_fields(["temp_c"]) == {"temp_c": 7.0}
    rows = await ts.aquery_recent_fields(["temp_c"], minutes=5)
    assert rows["temp_c"][0]["value"] == 7.0


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


def test_document_store_satisfies_protocol(config):
    assert isinstance(InMemoryDocumentAdapter(config), DocumentStore)


def test_events_come_back_newest_first(config):
    doc = InMemoryDocumentAdapter(config)
    for i in range(5):
        doc.log_event("reading", {"i": i})
    recent = doc.get_recent_events(3)
    assert [e["payload"]["i"] for e in recent] == [4, 3, 2]


def test_events_filter_by_type(config):
    doc = InMemoryDocumentAdapter(config)
    doc.log_event("reading", {"i": 1})
    doc.log_event("alarm", {"i": 2}, severity="critical")
    doc.log_event("reading", {"i": 3})

    alarms = doc.get_events_by_type("alarm")
    assert len(alarms) == 1
    assert alarms[0]["severity"] == "critical"
    assert [e["payload"]["i"] for e in doc.get_events_by_type("reading")] == [3, 1]


def test_event_log_is_bounded(config):
    doc = InMemoryDocumentAdapter(config, max_events=5)
    for i in range(20):
        doc.log_event("reading", {"i": i})
    assert len(doc.all_events()) == 5
    assert doc.all_events()[0]["payload"]["i"] == 15


def test_metadata_merges_and_stamps_asset(config):
    doc = InMemoryDocumentAdapter(config)
    doc.upsert_asset_metadata({"vendor": "acme"})
    doc.upsert_asset_metadata({"site": "plant-a"})
    meta = doc.get_asset_metadata()
    assert meta["vendor"] == "acme"
    assert meta["site"] == "plant-a"
    assert meta["asset_id"] == "unit_001"


def test_returned_events_are_copies(config):
    doc = InMemoryDocumentAdapter(config)
    doc.log_event("reading", {"i": 1})
    doc.get_recent_events()[0]["event_type"] = "tampered"
    assert doc.get_recent_events()[0]["event_type"] == "reading"


@pytest.mark.asyncio
async def test_document_async_surface(config):
    doc = InMemoryDocumentAdapter(config)
    await doc.alog_event("reading", {"i": 1})
    assert len(await doc.aget_recent_events()) == 1
    assert len(await doc.aget_events_by_type("reading")) == 1
    await doc.aupsert_asset_metadata({"vendor": "acme"})
    assert (await doc.aget_asset_metadata())["vendor"] == "acme"


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def test_cache_satisfies_protocol(config):
    assert isinstance(InMemoryCacheAdapter(config), CacheStore)


def test_cache_round_trips_structured_values(config):
    cache = InMemoryCacheAdapter(config)
    cache.set_latest("profile", {"segment": "eco", "score": 0.4})
    assert cache.get_latest_cached("profile") == {"segment": "eco", "score": 0.4}
    assert cache.get_latest_cached("absent") is None


def test_cache_state_defaults_to_unknown(config):
    cache = InMemoryCacheAdapter(config)
    assert cache.get_state() == "unknown"
    cache.set_state("running")
    assert cache.get_state() == "running"


def test_ttl_expires_keys(config):
    cache = InMemoryCacheAdapter(config)
    cache._client.setex("k", 1, "v")
    assert cache._client.get("k") == "v"
    cache._client._expiries["k"] = time.time() - 1  # fast-forward past expiry
    assert cache._client.get("k") is None
    assert "k" not in cache._client.keys("*")


def test_key_pattern_matching(config):
    cache = InMemoryCacheAdapter(config)
    cache._client.set("session:a", "1")
    cache._client.set("session:b", "2")
    cache._client.set("other", "3")
    assert sorted(cache._client.keys("session:*")) == ["session:a", "session:b"]
    assert cache._client.delete("session:a") == 1
    assert cache._client.keys("session:*") == ["session:b"]


@pytest.mark.asyncio
async def test_publish_is_retained_for_readback(config):
    cache = InMemoryCacheAdapter(config)
    await cache.publish("telemetry", {"temp_c": 4.0})
    assert cache.recent_published() == [("telemetry", {"temp_c": 4.0})]


@pytest.mark.asyncio
async def test_cache_async_surface(config):
    cache = InMemoryCacheAdapter(config)
    await cache.aset_latest("f", 1.0)
    assert await cache.aget_latest_cached("f") == 1.0
    await cache.aset_state("degraded")
    assert await cache.aget_state() == "degraded"


# --------------------------------------------------------------------------- #
# The reason the cache exposes a redis-shaped client at all
# --------------------------------------------------------------------------- #


def test_session_store_gets_full_behaviour_on_memory_cache(config):
    store: SessionStore[SessionContext] = SessionStore(InMemoryCacheAdapter(config))
    first = store.new_session(primary_entity_id="cust_1")
    store.new_session(primary_entity_id="cust_2")

    assert store.load(first.session_id).primary_entity_id == "cust_1"
    assert len(store.list_active()) == 2

    store.delete(first.session_id)
    assert store.load(first.session_id) is None
    assert len(store.list_active()) == 1


# --------------------------------------------------------------------------- #
# Objects
# --------------------------------------------------------------------------- #


def test_object_store_round_trip(config, tmp_path):
    source = tmp_path / "policy.zip"
    source.write_bytes(b"weights")
    store = InMemoryObjectAdapter(config)

    key = store.upload_file(str(source))
    assert key == "unit_001/policy.zip"
    assert store.list_files() == ["policy.zip"]

    target = tmp_path / "restored" / "policy.zip"
    store.download_file("policy.zip", str(target))
    assert target.read_bytes() == b"weights"

    store.delete_file("policy.zip")
    assert store.list_files() == []


def test_missing_object_raises(config):
    with pytest.raises(FileNotFoundError):
        InMemoryObjectAdapter(config).download_file("nope.zip", "/tmp/nope.zip")


def test_file_backed_store_persists_across_instances(config, tmp_path):
    source = tmp_path / "reward.pt"
    source.write_bytes(b"reward")
    root = str(tmp_path / "objects")

    FileBackedObjectAdapter(config, root=root).upload_file(str(source))

    reopened = FileBackedObjectAdapter(config, root=root)
    assert reopened.list_files() == ["reward.pt"]
    target = tmp_path / "out.pt"
    reopened.download_file("reward.pt", str(target))
    assert target.read_bytes() == b"reward"
    assert os.path.isfile(os.path.join(root, "unit_001", "reward.pt"))
