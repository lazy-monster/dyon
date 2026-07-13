"""MQTT ingestor with Pydantic schema validation and dead-letter routing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent
from dyon.network.transport import MQTTTransport

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.writer import TelemetryRouter

log = logging.getLogger(__name__)


class MQTTIngestor(LayerBase):
    """
    Subscribes to the twin's telemetry topic, validates incoming payloads,
    and routes valid messages to the TelemetryRouter.

    Invalid messages are forwarded to the dead-letter topic.
    """

    layer_name = "network"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        router: TelemetryRouter,
        schema_validator: Callable[[dict], dict] | None = None,
    ):
        super().__init__(config, event_bus)
        self.router = router
        self._validator = schema_validator or self._default_validator
        self._transport = MQTTTransport(config, role="ingestor")
        self._dead_letter_topic = f"dt/{config.asset_id}/dead_letter"
        self._loop: asyncio.AbstractEventLoop | None = None

    def _default_validator(self, payload: dict) -> dict:
        """Keep any numeric value plus the ``fault_injected`` marker.

        The downstream ``TelemetryRouter`` filters to ``config.field_names``
        before writing to InfluxDB, so accepting extra numeric keys here is
        harmless and lets simulators publish auxiliary signals (e.g. computed
        diagnostics) without having to declare every one in ``sensor_fields``.
        Replace this validator if you want strict-by-name filtering.
        """
        return {
            k: v
            for k, v in payload.items()
            if isinstance(v, int | float | bool) or k == "fault_injected"
        }

    def _on_message(self, payload: dict) -> None:
        try:
            validated = self._validator(payload)
        except Exception as e:
            log.warning("Invalid telemetry payload: %s — %s", payload, e)
            self._transport.publish(
                self._dead_letter_topic,
                {"error": str(e), "payload": payload},
            )
            return

        if self._loop is None:
            log.warning("Message received before event loop was captured; dropping")
            return

        asyncio.run_coroutine_threadsafe(self.router.route(validated), self._loop)
        asyncio.run_coroutine_threadsafe(
            self.bus.publish(
                DomainEvent(
                    event_type="telemetry.received",
                    source_layer="network",
                    source_asset=self.config.asset_id,
                    payload={"field_count": len(validated)},
                )
            ),
            self._loop,
        )

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._running = True
        # connect() now blocks on initial-connect retries; keep the loop free.
        await asyncio.to_thread(self._transport.connect)
        self._transport.subscribe(self.config.topic_telemetry, self._on_message)
        self.log.info(
            "MQTT ingestor listening on '%s'", self.config.topic_telemetry
        )
        while self._running:
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False
        self._transport.disconnect()
