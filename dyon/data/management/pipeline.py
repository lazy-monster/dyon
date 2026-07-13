"""Data management pipeline: smoothing, rate-of-change, quality flags, health score."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import numpy as np

from dyon.core.base import LayerBase
from dyon.data.management.health import HealthScoreCalculator

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, TimeSeriesStore

log = logging.getLogger(__name__)


class DataManagementPipeline(LayerBase):
    """
    Periodic pipeline: rolling smoothing, rate-of-change, quality flags,
    composite health score.
    """

    layer_name = "data_management"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        cache: CacheStore,
        interval: int = 10,
        smooth_window: int = 5,
        lookback_minutes: int = 5,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.interval = interval
        self.smooth_window = smooth_window
        self.lookback_minutes = lookback_minutes
        self.health_calc = HealthScoreCalculator(config.thresholds)

    async def run_once(self) -> None:
        # One batched, thread-offloaded query covers every field's recent window
        # — replaces a blocking per-field round trip on the event loop.
        recent = await self.ts.aquery_recent_fields(
            self.config.field_names, minutes=self.lookback_minutes
        )

        processed = {}
        for fname in self.config.field_names:
            try:
                points = recent.get(fname) or []
                if len(points) < 3:
                    continue
                vals = [p["value"] for p in points]
                window = min(self.smooth_window, len(vals))
                smoothed = float(
                    np.convolve(vals, np.ones(window) / window, mode="valid")[-1]
                )
                roc = float(vals[-1] - vals[-2])
                processed[f"{fname}_smooth"] = round(smoothed, 4)
                processed[f"{fname}_roc"] = round(roc, 4)
                processed[f"{fname}_quality"] = self._quality_flag(fname, vals[-1])
            except Exception as e:
                log.warning("Pipeline processing error for '%s': %s", fname, e)

        if processed:
            await self.ts.awrite_point("asset_processed", processed)
            for k, v in processed.items():
                await self.cache.aset_latest(k, v)

        # Health uses the stores' wider latest-value window (not the short
        # smoothing lookback), so a field that pauses briefly between reports
        # still counts toward the score.
        latest = await self.ts.aget_latest_fields(self.config.field_names)
        health = self.health_calc.compute(latest)
        await self.ts.awrite_point("asset_health", {"health_score": health})
        await self.cache.aset_latest("health_score", health)

    def _quality_flag(self, field: str, value: float) -> float:
        t = self.config.thresholds.get(field)
        if not t:
            return 1.0
        low = t.get("low", False)
        crit_t, warn_t = t.get("crit"), t.get("warn")
        if crit_t is not None and ((low and value < crit_t) or (not low and value > crit_t)):
            return 3.0
        if warn_t is not None and ((low and value < warn_t) or (not low and value > warn_t)):
            return 2.0
        return 1.0

    async def start(self) -> None:
        self._running = True
        self.log.info("Data management pipeline started (interval=%ss)", self.interval)
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self.log.error("Pipeline error: %s", e)
            await asyncio.sleep(self.interval)
