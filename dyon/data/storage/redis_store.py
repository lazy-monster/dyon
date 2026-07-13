"""Redis cache and pub/sub adapter."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from dyon.core import metrics

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class RedisAdapter:
    """Redis adapter for fast state cache and pub/sub."""

    def __init__(self, config: TwinConfig):
        from dyon._compat import require
        redis_lib = require("redis", "stores")

        self._cfg = config.redis
        self._asset_id = config.asset_id
        self._client = redis_lib.Redis.from_url(
            self._cfg.url, db=self._cfg.db, decode_responses=True
        )
        self._prefix = f"dt:{self._asset_id}"

    def set_latest(self, field: str, value: Any) -> None:
        try:
            self._client.set(f"{self._prefix}:latest:{field}", json.dumps(value))
        except Exception as e:
            metrics.increment("storage.redis.write_errors")
            log.warning("Redis set_latest failed for '%s' (data dropped): %s", field, e)

    def get_latest_cached(self, field: str) -> Any:
        try:
            raw = self._client.get(f"{self._prefix}:latest:{field}")
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            log.debug("Redis get_latest_cached error for '%s': %s", field, e)
        return None

    def set_state(self, state: str) -> None:
        try:
            self._client.set(f"{self._prefix}:state", state)
        except Exception as e:
            log.debug("Redis set_state error: %s", e)

    def get_state(self) -> str:
        try:
            val = self._client.get(f"{self._prefix}:state")
            return val or "unknown"
        except Exception as e:
            log.debug("Redis get_state error: %s", e)
            return "unknown"

    # --- async wrappers -------------------------------------------------------
    # redis-py is synchronous; these offload the blocking call to a worker thread
    # so the event loop is never frozen on a slow Redis hop. The hot layer loops
    # await these; sync callers (thread-offloaded tools) keep the sync methods.

    async def aset_latest(self, field: str, value: Any) -> None:
        await asyncio.to_thread(self.set_latest, field, value)

    async def aget_latest_cached(self, field: str) -> Any:
        return await asyncio.to_thread(self.get_latest_cached, field)

    async def aset_state(self, state: str) -> None:
        await asyncio.to_thread(self.set_state, state)

    async def aget_state(self) -> str:
        return await asyncio.to_thread(self.get_state)

    async def publish(self, channel: str, message: dict) -> None:
        # The underlying redis-py client is synchronous; run it in the default
        # executor so a slow Redis hop cannot stall the asyncio event loop.
        payload = json.dumps(message, default=str)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self._client.publish(channel, payload)
            )
        except Exception as e:
            log.debug("Redis publish error on '%s': %s", channel, e)

    def close(self) -> None:
        self._client.close()
