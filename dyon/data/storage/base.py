"""Storage backend protocols."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TimeSeriesStore(Protocol):
    """Writes and queries time-series sensor data."""

    def write_point(
        self,
        measurement: str,
        fields: dict[str, float],
        tags: dict[str, str] | None = None,
    ) -> None: ...

    def query_recent(
        self,
        field: str,
        minutes: int = 10,
        measurement: str = "asset_telemetry",
    ) -> Any: ...  # returns pd.DataFrame

    def get_latest(
        self,
        field: str,
        measurement: str = "asset_telemetry",
    ) -> float | None: ...

    def query_recent_fields(
        self,
        fields: list[str],
        minutes: int = 60,
        measurement: str = "asset_telemetry",
    ) -> dict[str, list[dict]]: ...

    def get_latest_fields(
        self, fields: list[str], measurement: str = "asset_telemetry"
    ) -> dict[str, float | None]: ...

    # Async variants for use inside the asyncio layer loops — they offload the
    # blocking client call to a worker thread so the event loop never stalls.
    # Sync callers (LangChain tools, already thread-offloaded) keep the methods
    # above.
    async def awrite_point(
        self,
        measurement: str,
        fields: dict[str, float],
        tags: dict[str, str] | None = None,
    ) -> None: ...
    async def aget_latest(
        self, field: str, measurement: str = "asset_telemetry"
    ) -> float | None: ...
    async def aquery_recent_fields(
        self,
        fields: list[str],
        minutes: int = 60,
        measurement: str = "asset_telemetry",
    ) -> dict[str, list[dict]]: ...
    async def aget_latest_fields(
        self, fields: list[str], measurement: str = "asset_telemetry"
    ) -> dict[str, float | None]: ...

    def close(self) -> None: ...


@runtime_checkable
class DocumentStore(Protocol):
    """Stores metadata documents and discrete events."""

    def upsert_asset_metadata(self, data: dict) -> None: ...
    def get_asset_metadata(self) -> dict: ...
    def log_event(
        self, event_type: str, payload: dict, severity: str = "info"
    ) -> None: ...
    def get_recent_events(self, n: int = 20) -> list[dict]: ...
    def get_events_by_type(self, event_type: str, n: int = 20) -> list[dict]: ...
    # Async variants (offload to a worker thread) for the asyncio layer loops.
    async def aupsert_asset_metadata(self, data: dict) -> None: ...
    async def alog_event(
        self, event_type: str, payload: dict, severity: str = "info"
    ) -> None: ...
    async def aget_recent_events(self, n: int = 20) -> list[dict]: ...
    async def aget_events_by_type(
        self, event_type: str, n: int = 20
    ) -> list[dict]: ...
    def close(self) -> None: ...


@runtime_checkable
class CacheStore(Protocol):
    """Fast key-value cache and state management."""

    def set_latest(self, field: str, value: Any) -> None: ...
    def get_latest_cached(self, field: str) -> Any: ...
    def set_state(self, state: str) -> None: ...
    def get_state(self) -> str: ...
    async def publish(self, channel: str, message: dict) -> None: ...
    # Async variants (offload to a worker thread) for the asyncio layer loops.
    async def aset_latest(self, field: str, value: Any) -> None: ...
    async def aget_latest_cached(self, field: str) -> Any: ...
    async def aset_state(self, state: str) -> None: ...
    async def aget_state(self) -> str: ...
    def close(self) -> None: ...


@runtime_checkable
class ObjectStore(Protocol):
    """Binary / file storage (models, firmware, images)."""

    def upload_file(
        self, local_path: str, object_name: str | None = None
    ) -> str: ...
    def download_file(self, object_name: str, local_path: str) -> None: ...
    def list_files(self) -> list[str]: ...
    def delete_file(self, object_name: str) -> None: ...
    def close(self) -> None: ...
