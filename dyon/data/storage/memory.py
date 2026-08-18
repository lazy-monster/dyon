"""In-process storage backends.

Every other adapter in this package speaks to a server: Influx, Mongo, Redis,
MinIO. That is the right default for a deployed twin, but it makes three other
situations awkward — a demo on a laptop, a test that wants real store semantics
rather than a stub, and an edge deployment with nowhere to put a database. The
adapters here close that gap. They implement the same protocols as their
networked counterparts (:mod:`dyon.data.storage.base`), including the ``a*``
coroutine variants the layer loops await, and hold their data in bounded
in-process structures.

They are real stores, not fakes: readings carry timestamps and are queried by
window, events are ordered and filterable by type, and cache keys expire. What
they do not do is survive the process, so a twin that must remember anything
across restarts wants the networked adapters.

::

    from dyon.data import InMemoryCacheAdapter, InMemoryDocumentAdapter

    cache = InMemoryCacheAdapter(config)
    doc = InMemoryDocumentAdapter(config)

:class:`InMemoryCacheAdapter` deliberately exposes a ``_client`` attribute
speaking the small slice of the redis-py API that
:class:`~dyon.session.context.SessionStore` reaches for when it is available
(``setex``/``get``/``delete``/``keys``), so sessions expire and can be listed
here exactly as they do against Redis.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)

DEFAULT_MAX_POINTS = 50_000
DEFAULT_MAX_EVENTS = 10_000


def _as_epoch(timestamp: float | datetime | None) -> float:
    """Normalise the three timestamp forms callers pass into epoch seconds."""
    if timestamp is None:
        return time.time()
    if isinstance(timestamp, datetime):
        return timestamp.timestamp()
    return float(timestamp)


class InMemoryTimeSeriesAdapter:
    """Time-series store backed by a bounded deque per ``(measurement, field)``.

    ``max_points`` caps how many samples each series retains; the oldest are
    discarded first, so a long-running twin has a flat memory profile rather
    than a growing one.
    """

    def __init__(
        self, config: TwinConfig, *, max_points: int = DEFAULT_MAX_POINTS
    ) -> None:
        self._asset_id = config.asset_id
        self._max_points = max_points
        self._series: dict[tuple[str, str], deque[tuple[float, float]]] = {}
        self._lock = threading.Lock()

    def _key(self, measurement: str, field: str) -> tuple[str, str]:
        return (measurement, field)

    def write_point(
        self,
        measurement: str,
        fields: dict[str, float],
        tags: dict[str, str] | None = None,
        timestamp: float | datetime | None = None,
    ) -> None:
        ts = _as_epoch(timestamp)
        with self._lock:
            for name, value in fields.items():
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    # Match the networked adapters: a bad reading is dropped
                    # with a warning rather than taking the writer down.
                    log.warning(
                        "In-memory write skipped non-numeric field '%s'=%r",
                        name, value,
                    )
                    continue
                series = self._series.get(self._key(measurement, name))
                if series is None:
                    series = deque(maxlen=self._max_points)
                    self._series[self._key(measurement, name)] = series
                series.append((ts, numeric))

    def query_recent(
        self,
        field: str,
        minutes: int = 10,
        measurement: str = "asset_telemetry",
    ) -> Any:
        """Return a DataFrame with ``_time``/``_value`` columns.

        The column names mirror Influx's Flux output so that consumers such as
        the forecaster read either backend without branching.
        """
        import pandas as pd

        rows = self._window(field, minutes, measurement)
        if not rows:
            return pd.DataFrame(columns=["_time", "_value", "_field"])
        return pd.DataFrame({
            "_time":  [datetime.fromtimestamp(ts, UTC) for ts, _ in rows],
            "_value": [value for _, value in rows],
            "_field": [field] * len(rows),
        })

    def _window(
        self, field: str, minutes: int, measurement: str
    ) -> list[tuple[float, float]]:
        cutoff = time.time() - minutes * 60
        with self._lock:
            series = self._series.get(self._key(measurement, field))
            snapshot = list(series) if series else []
        return [(ts, value) for ts, value in snapshot if ts >= cutoff]

    def get_latest(
        self, field: str, measurement: str = "asset_telemetry"
    ) -> float | None:
        with self._lock:
            series = self._series.get(self._key(measurement, field))
            return series[-1][1] if series else None

    def query_recent_fields(
        self,
        fields: list[str],
        minutes: int = 60,
        measurement: str = "asset_telemetry",
    ) -> dict[str, list[dict]]:
        return {
            field: [
                {"ts": ts, "value": value}
                for ts, value in self._window(field, minutes, measurement)
            ]
            for field in fields
        }

    def get_latest_fields(
        self, fields: list[str], measurement: str = "asset_telemetry"
    ) -> dict[str, float | None]:
        return {field: self.get_latest(field, measurement) for field in fields}

    # --- async surface --------------------------------------------------------
    # These are already non-blocking (no I/O), so unlike the networked adapters
    # they answer directly instead of hopping to a worker thread.

    async def awrite_point(
        self,
        measurement: str,
        fields: dict[str, float],
        tags: dict[str, str] | None = None,
        timestamp: float | datetime | None = None,
    ) -> None:
        self.write_point(measurement, fields, tags, timestamp)

    async def aget_latest(
        self, field: str, measurement: str = "asset_telemetry"
    ) -> float | None:
        return self.get_latest(field, measurement)

    async def aquery_recent(
        self, field: str, minutes: int = 10, measurement: str = "asset_telemetry"
    ) -> Any:
        return self.query_recent(field, minutes, measurement)

    async def aquery_recent_fields(
        self,
        fields: list[str],
        minutes: int = 60,
        measurement: str = "asset_telemetry",
    ) -> dict[str, list[dict]]:
        return self.query_recent_fields(fields, minutes, measurement)

    async def aget_latest_fields(
        self, fields: list[str], measurement: str = "asset_telemetry"
    ) -> dict[str, float | None]:
        return self.get_latest_fields(fields, measurement)

    def close(self) -> None:
        with self._lock:
            self._series.clear()


class InMemoryDocumentAdapter:
    """Document store holding asset metadata and a bounded event log."""

    def __init__(
        self, config: TwinConfig, *, max_events: int = DEFAULT_MAX_EVENTS
    ) -> None:
        self._asset_id = config.asset_id
        self._metadata: dict = {}
        self._events: deque[dict] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def upsert_asset_metadata(self, data: dict) -> None:
        with self._lock:
            self._metadata.update(data)
            self._metadata["asset_id"] = self._asset_id
            self._metadata["updated_at"] = datetime.now(UTC)

    def get_asset_metadata(self) -> dict:
        with self._lock:
            return dict(self._metadata)

    def log_event(
        self, event_type: str, payload: dict, severity: str = "info"
    ) -> None:
        with self._lock:
            self._events.append({
                "asset_id":   self._asset_id,
                "event_type": event_type,
                "payload":    payload,
                "severity":   severity,
                "timestamp":  datetime.now(UTC),
            })

    def get_recent_events(self, n: int = 20) -> list[dict]:
        with self._lock:
            return [dict(e) for e in list(self._events)[-n:][::-1]]

    def get_events_by_type(self, event_type: str, n: int = 20) -> list[dict]:
        with self._lock:
            matches = [e for e in self._events if e["event_type"] == event_type]
        return [dict(e) for e in matches[-n:][::-1]]

    def all_events(self) -> list[dict]:
        """Every retained event, oldest first.

        Not part of the ``DocumentStore`` protocol — offered because offline
        analytics and corpus builders otherwise have to page through
        ``get_events_by_type`` to reconstruct what this store already holds.
        """
        with self._lock:
            return [dict(e) for e in self._events]

    # --- async surface --------------------------------------------------------

    async def aupsert_asset_metadata(self, data: dict) -> None:
        self.upsert_asset_metadata(data)

    async def aget_asset_metadata(self) -> dict:
        return self.get_asset_metadata()

    async def alog_event(
        self, event_type: str, payload: dict, severity: str = "info"
    ) -> None:
        self.log_event(event_type, payload, severity)

    async def aget_recent_events(self, n: int = 20) -> list[dict]:
        return self.get_recent_events(n)

    async def aget_events_by_type(self, event_type: str, n: int = 20) -> list[dict]:
        return self.get_events_by_type(event_type, n)

    def close(self) -> None:
        with self._lock:
            self._events.clear()
            self._metadata.clear()


class _MemoryKeyValueClient:
    """The slice of the redis-py client surface that framework code probes for.

    :class:`~dyon.session.context.SessionStore` uses ``setex``/``get``/
    ``delete``/``keys`` when the cache exposes a native client and silently
    loses expiry and listing when it does not. Implementing those four here
    means a session behaves the same in-process as it does against Redis.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._expiries: dict[str, float] = {}
        self._lock = threading.Lock()

    def _expired(self, key: str, now: float) -> bool:
        expiry = self._expiries.get(key)
        return expiry is not None and expiry <= now

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k in self._values if self._expired(k, now)]:
            self._values.pop(key, None)
            self._expiries.pop(key, None)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._values[key] = value
            self._expiries.pop(key, None)

    def setex(self, key: str, ttl: int, value: str) -> None:
        with self._lock:
            self._values[key] = value
            self._expiries[key] = time.time() + ttl

    def get(self, key: str) -> str | None:
        with self._lock:
            if self._expired(key, time.time()):
                self._values.pop(key, None)
                self._expiries.pop(key, None)
                return None
            return self._values.get(key)

    def delete(self, *keys: str) -> int:
        removed = 0
        with self._lock:
            for key in keys:
                if self._values.pop(key, None) is not None:
                    removed += 1
                self._expiries.pop(key, None)
        return removed

    def keys(self, pattern: str = "*") -> list[str]:
        with self._lock:
            self._purge()
            return [k for k in self._values if fnmatch.fnmatchcase(k, pattern)]

    def publish(self, channel: str, payload: str) -> int:
        # No subscribers in-process; the count mirrors redis-py's return value.
        return 0

    def close(self) -> None:
        with self._lock:
            self._values.clear()
            self._expiries.clear()


class InMemoryCacheAdapter:
    """Cache and state store with TTL support and redis-compatible key access."""

    def __init__(self, config: TwinConfig) -> None:
        self._asset_id = config.asset_id
        self._prefix = f"dt:{config.asset_id}"
        self._client = _MemoryKeyValueClient()
        self._state = "unknown"
        # Messages published while nothing is listening are still worth keeping:
        # the offline dashboards read them back as a poor man's pub/sub log.
        self._published: deque[tuple[str, dict]] = deque(maxlen=500)

    def set_latest(self, field: str, value: Any) -> None:
        self._client.set(f"{self._prefix}:latest:{field}", json.dumps(value, default=str))

    def get_latest_cached(self, field: str) -> Any:
        raw = self._client.get(f"{self._prefix}:latest:{field}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def set_state(self, state: str) -> None:
        self._state = state

    def get_state(self) -> str:
        return self._state

    async def publish(self, channel: str, message: dict) -> None:
        self._published.append((channel, dict(message)))

    def recent_published(self, n: int = 50) -> list[tuple[str, dict]]:
        """The last ``n`` published messages, oldest first."""
        return list(self._published)[-n:]

    # --- async surface --------------------------------------------------------

    async def aset_latest(self, field: str, value: Any) -> None:
        self.set_latest(field, value)

    async def aget_latest_cached(self, field: str) -> Any:
        return self.get_latest_cached(field)

    async def aset_state(self, state: str) -> None:
        self.set_state(state)

    async def aget_state(self) -> str:
        return self.get_state()

    def close(self) -> None:
        self._client.close()
        self._published.clear()


class InMemoryObjectAdapter:
    """Object store that keeps uploaded bytes in a dictionary.

    Enough to exercise the versioned-corpus path (:mod:`dyon.ml.corpus`)
    without a MinIO server; ``download_file`` writes the retained bytes back to
    disk so a caller that expects a real file gets one.
    """

    def __init__(self, config: TwinConfig) -> None:
        self._asset_id = config.asset_id
        self._objects: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def _object_key(self, name: str) -> str:
        return f"{self._asset_id}/{name}"

    def upload_file(self, local_path: str, object_name: str | None = None) -> str:
        name = object_name or os.path.basename(local_path)
        key = self._object_key(name)
        with open(local_path, "rb") as fh:
            data = fh.read()
        with self._lock:
            self._objects[key] = data
        return key

    def download_file(self, object_name: str, local_path: str) -> None:
        key = self._object_key(object_name)
        with self._lock:
            data = self._objects.get(key)
        if data is None:
            raise FileNotFoundError(f"No object '{key}' in the in-memory store")
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(data)

    def list_files(self) -> list[str]:
        prefix = f"{self._asset_id}/"
        with self._lock:
            return sorted(
                key[len(prefix):] for key in self._objects if key.startswith(prefix)
            )

    def delete_file(self, object_name: str) -> None:
        with self._lock:
            self._objects.pop(self._object_key(object_name), None)

    def close(self) -> None:
        with self._lock:
            self._objects.clear()


class FileBackedObjectAdapter(InMemoryObjectAdapter):
    """Object store that persists to a local directory.

    Same protocol as :class:`InMemoryObjectAdapter`, but the bytes land under
    ``root``, so trained policies and recovered rewards survive a restart on a
    machine with no MinIO.
    """

    def __init__(self, config: TwinConfig, root: str = "./object_store") -> None:
        super().__init__(config)
        self._root = os.path.abspath(root)
        os.makedirs(self._root, exist_ok=True)

    def _path(self, object_name: str) -> str:
        return os.path.join(self._root, self._object_key(object_name))

    def upload_file(self, local_path: str, object_name: str | None = None) -> str:
        name = object_name or os.path.basename(local_path)
        destination = self._path(name)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(local_path, destination)
        return self._object_key(name)

    def download_file(self, object_name: str, local_path: str) -> None:
        source = self._path(object_name)
        if not os.path.isfile(source):
            raise FileNotFoundError(f"No object '{object_name}' under {self._root}")
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copyfile(source, local_path)

    def list_files(self) -> list[str]:
        base = os.path.join(self._root, self._asset_id)
        if not os.path.isdir(base):
            return []
        found: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                found.append(os.path.relpath(full, base))
        return sorted(found)

    def delete_file(self, object_name: str) -> None:
        path = self._path(object_name)
        if os.path.isfile(path):
            os.remove(path)

    def close(self) -> None:
        # Files outlive the process by design, so there is nothing to release.
        return None


__all__ = [
    "FileBackedObjectAdapter",
    "InMemoryCacheAdapter",
    "InMemoryDocumentAdapter",
    "InMemoryObjectAdapter",
    "InMemoryTimeSeriesAdapter",
]
