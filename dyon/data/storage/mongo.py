"""MongoDB document store adapter."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from dyon.core import metrics

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class MongoAdapter:
    """MongoDB adapter for asset metadata and event documents."""

    def __init__(self, config: TwinConfig):
        from dyon._compat import require
        require("pymongo", "stores")
        from pymongo import MongoClient

        self._cfg = config.mongo
        self._asset_id = config.asset_id
        self._client: MongoClient = MongoClient(self._cfg.uri)
        self._db = self._client[self._cfg.db]
        self._assets = self._db["assets"]
        self._events = self._db["events"]

    def upsert_asset_metadata(self, data: dict) -> None:
        try:
            self._assets.update_one(
                {"asset_id": self._asset_id},
                {"$set": {**data, "asset_id": self._asset_id,
                           "updated_at": datetime.now(UTC)}},
                upsert=True,
            )
        except Exception as e:
            metrics.increment("storage.mongo.write_errors")
            log.warning("Mongo upsert failed (data dropped): %s", e)

    def get_asset_metadata(self) -> dict:
        try:
            doc = self._assets.find_one(
                {"asset_id": self._asset_id}, {"_id": 0}
            )
            return doc or {}
        except Exception as e:
            log.error("MongoDB get_asset_metadata error: %s", e)
            return {}

    def log_event(
        self, event_type: str, payload: dict, severity: str = "info"
    ) -> None:
        try:
            self._events.insert_one({
                "asset_id": self._asset_id,
                "event_type": event_type,
                "payload": payload,
                "severity": severity,
                "timestamp": datetime.now(UTC),
            })
        except Exception as e:
            metrics.increment("storage.mongo.write_errors")
            log.warning("Mongo log_event failed (event dropped): %s", e)

    def get_recent_events(self, n: int = 20) -> list[dict]:
        try:
            cursor = (
                self._events
                .find({"asset_id": self._asset_id}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(n)
            )
            return list(cursor)
        except Exception as e:
            log.error("MongoDB get_recent_events error: %s", e)
            return []

    def get_events_by_type(self, event_type: str, n: int = 20) -> list[dict]:
        """Return the most recent ``n`` events of a specific type, newest first."""
        try:
            cursor = (
                self._events
                .find(
                    {"asset_id": self._asset_id, "event_type": event_type},
                    {"_id": 0},
                )
                .sort("timestamp", -1)
                .limit(n)
            )
            return list(cursor)
        except Exception as e:
            log.error("MongoDB get_events_by_type error: %s", e)
            return []

    # --- async wrappers -------------------------------------------------------
    # pymongo is synchronous; these offload the blocking call to a worker thread
    # so the event loop is never frozen on a Mongo round trip. The hot layer loops
    # await these; sync callers (thread-offloaded tools) keep the sync methods.

    async def aupsert_asset_metadata(self, data: dict) -> None:
        await asyncio.to_thread(self.upsert_asset_metadata, data)

    async def aget_asset_metadata(self) -> dict:
        return await asyncio.to_thread(self.get_asset_metadata)

    async def alog_event(
        self, event_type: str, payload: dict, severity: str = "info"
    ) -> None:
        await asyncio.to_thread(self.log_event, event_type, payload, severity)

    async def aget_recent_events(self, n: int = 20) -> list[dict]:
        return await asyncio.to_thread(self.get_recent_events, n)

    async def aget_events_by_type(self, event_type: str, n: int = 20) -> list[dict]:
        return await asyncio.to_thread(self.get_events_by_type, event_type, n)

    def close(self) -> None:
        self._client.close()
