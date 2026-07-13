"""TelemetryRouter: validates and fans out incoming telemetry to storage backends."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from dyon.core import metrics
from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore

log = logging.getLogger(__name__)


class TelemetryRouter(LayerBase):
    """
    Receives validated telemetry and fans it out to the appropriate stores.

    This is the entry point for data arriving from the network layer.
    """

    layer_name = "data"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        doc_store: DocumentStore,
        cache: CacheStore,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.doc = doc_store
        self.cache = cache
        # Bound at 1000 to apply backpressure: if downstream processing stalls,
        # put() will block the ingest caller rather than growing memory unboundedly.
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    async def route(self, data: dict) -> None:
        """Enqueue incoming telemetry for async processing."""
        await self._queue.put(data)

    async def _process(self, data: dict) -> None:
        fields = {
            k: float(v)
            for k, v in data.items()
            if k in self.config.field_names and v is not None
        }

        if fields:
            await self.ts.awrite_point("asset_telemetry", fields)
            for k, v in fields.items():
                await self.cache.aset_latest(k, v)

        if data.get("fault_injected"):
            await self.doc.alog_event(
                "fault_injected", {"readings": fields}, severity="warning"
            )

        await self.cache.aset_latest("last_seen", time.time())

        await self.bus.publish(
            DomainEvent(
                event_type="telemetry.routed",
                source_layer="data",
                source_asset=self.config.asset_id,
                payload=fields,
            )
        )

    async def start(self) -> None:
        self._running = True
        self.log.info("TelemetryRouter started")
        while self._running:
            try:
                # 1-second timeout lets the loop re-check _running without blocking
                # indefinitely when the queue is empty (avoids a stuck shutdown).
                data = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process(data)
                self._queue.task_done()
            except TimeoutError:
                pass
            except Exception as e:
                metrics.increment("telemetry.dropped")
                self.log.error(
                    "TelemetryRouter error (item dropped): %s — keys=%s",
                    e, sorted(data)[:8],
                )

    async def stop(self) -> None:
        self._running = False
        # Drain what's queued, but never let one poison item discard the rest,
        # and never hang shutdown for more than a few seconds.
        deadline = asyncio.get_running_loop().time() + 5.0
        while not self._queue.empty():
            if asyncio.get_running_loop().time() > deadline:
                self.log.warning("Drain deadline hit; %d items discarded", self._queue.qsize())
                metrics.increment("telemetry.dropped", self._queue.qsize())
                break
            data = self._queue.get_nowait()
            try:
                await self._process(data)
            except Exception as e:
                metrics.increment("telemetry.dropped")
                self.log.error("Drain error (item dropped): %s", e)
            finally:
                self._queue.task_done()
