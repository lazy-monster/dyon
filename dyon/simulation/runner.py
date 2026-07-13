"""ModelRunner: runs all registered models in a loop and writes residuals."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent
from dyon.simulation.base import TwinModel

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import TimeSeriesStore

log = logging.getLogger(__name__)


class ModelRunner(LayerBase):
    """
    Runs all registered TwinModel instances in a periodic loop.

    For each model:
    1. Gathers current inputs from InfluxDB
    2. Steps the model forward by 1 second
    3. Writes predictions to ``asset_simulation_{model_name}``
    4. Computes residuals and writes to ``asset_residuals_{model_name}``
    5. Publishes an event if any residual exceeds the anomaly threshold
    """

    layer_name = "simulation"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        models: list[TwinModel],
        step_interval: float = 1.0,
        residual_anomaly_threshold: float = 10.0,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.models = {m.model_name: m for m in models}
        self.step_interval = step_interval
        self.residual_threshold = residual_anomaly_threshold

    async def start(self) -> None:
        self._running = True
        self.log.info(
            "ModelRunner started with models: %s", list(self.models.keys())
        )
        while self._running:
            # One batched query per cycle, off the event loop — reused for both
            # model inputs and the residual comparison below.
            latest = await self.ts.aget_latest_fields(self.config.field_names)
            for name, model in self.models.items():
                try:
                    inputs = {
                        fname: v for fname, v in latest.items() if v is not None
                    }

                    predicted = model.step(dt=self.step_interval, inputs=inputs)
                    if predicted:
                        await self.ts.awrite_point(
                            f"asset_simulation_{name}", predicted
                        )

                    # Compute and write residuals
                    residuals = {}
                    large_residuals = {}
                    for pred_key, pred_val in predicted.items():
                        real_field = pred_key.removeprefix("sim_")
                        real_val = (
                            latest[real_field]
                            if real_field in latest
                            else await self.ts.aget_latest(real_field)
                        )
                        if real_val is not None:
                            res = round(real_val - pred_val, 4)
                            residuals[f"res_{real_field}"] = res
                            if abs(res) > self.residual_threshold:
                                large_residuals[real_field] = res

                    if residuals:
                        await self.ts.awrite_point(
                            f"asset_residuals_{name}", residuals
                        )

                    if large_residuals:
                        await self.bus.publish(
                            DomainEvent(
                                event_type="simulation.anomaly_detected",
                                source_layer="simulation",
                                source_asset=self.config.asset_id,
                                payload={
                                    "model": name,
                                    "large_residuals": large_residuals,
                                },
                                severity="warning",
                            )
                        )

                except Exception as e:
                    self.log.error("Model '%s' step error: %s", name, e)

            await asyncio.sleep(self.step_interval)
