"""DittoSyncService: keeps the Eclipse Ditto Thing synchronised with live state."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore
    from dyon.services.ditto.client import DittoClient

log = logging.getLogger(__name__)


class DittoSyncService(LayerBase):
    """Keeps the Eclipse Ditto Thing synchronised with live twin state."""

    layer_name = "service_ditto"
    service_name = "ditto_sync"
    dependencies: list[str] = []

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        cache: CacheStore,
        ditto_client: DittoClient,
        doc_store: DocumentStore | None = None,
        sync_interval: int = 5,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.ditto = ditto_client
        self.doc = doc_store
        self.sync_interval = sync_interval
        # Set on stop() so the sleep between sync cycles wakes immediately
        # instead of blocking shutdown for up to sync_interval seconds.
        self._stop_event = asyncio.Event()

    async def initialise(self) -> None:
        await self.ditto.wait_for_ready()
        try:
            await self.ditto.create_policy()
        except Exception as e:
            self.log.error(
                "Ditto create_policy failed (continuing — sync loop may fail later): %s", e,
            )
        try:
            await self.ditto.create_thing(self.config)
        except Exception as e:
            self.log.error(
                "Ditto create_thing failed (continuing — sync loop may fail later): %s", e,
            )

    async def sync_once(self) -> None:
        # A field with no data must NOT be mirrored as a hard 0.0 (which would be
        # indistinguishable from a genuine zero reading); skip it so the Ditto
        # feature simply omits the field until real data arrives.
        latest = await self.ts.aget_latest_fields(self.config.field_names)
        telemetry = {f: v for f, v in latest.items() if v is not None}
        await self.ditto.update_feature("telemetry", telemetry)

        # ``or`` would flip a legitimate health=0.0 (every sensor critical)
        # silently to 100.0 (perfectly healthy). Use an explicit None check.
        health = await self.cache.aget_latest_cached("health_score")
        if health is None:
            health = 100.0
        state = await self.cache.aget_state()
        await self.ditto.update_feature(
            "health",
            {"health_score": health, "operational_state": state},
        )

    async def start(self) -> None:
        self._running = True
        self.log.info("DittoSyncService started (interval=%ds)", self.sync_interval)
        while self._running:
            try:
                await self.sync_once()
            except Exception as e:
                self.log.error("Ditto sync error: %s", e)
            # Wait the interval, but return early the moment stop() fires.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.sync_interval)

    async def stop(self) -> None:
        self._stop_event.set()
        await super().stop()
        # Release the reusable httpx client so it doesn't leak on shutdown.
        await self.ditto.aclose()
