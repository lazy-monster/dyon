"""Core abstract base classes for all DT layers and the twin itself."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus


class LayerBase(ABC):
    """Abstract base for all DT layers."""

    layer_name: str = "unnamed"

    def __init__(self, config: TwinConfig, event_bus: EventBus):
        self.config = config
        self.bus = event_bus
        self.log = logging.getLogger(
            f"dyon.{self.layer_name}.{config.asset_id}"
        )
        self._running = False

    async def initialise(self) -> None:  # noqa: B027 - optional hook, intentionally non-abstract
        """One-time setup (DB seeds, graph build, model load). Override if needed."""

    @abstractmethod
    async def start(self) -> None:
        """Begin the layer's continuous operation."""
        ...

    async def stop(self) -> None:
        """Graceful shutdown. Override to release resources."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


class AbstractDigitalTwin(ABC):
    """
    A complete digital twin instance.

    Subclass this to create an asset-specific twin.
    At minimum, provide the config and override build_layers().
    """

    def __init__(self, config: TwinConfig):
        from dyon.core.events import EventBus

        self.config = config
        self.bus = EventBus()
        self.layers: dict[str, LayerBase] = {}
        self._tasks: list[asyncio.Task] = []
        self.connectors: list = []       # populated by ConnectorRegistry
        self.log = logging.getLogger(
            f"dyon.twin.{config.asset_id}"
        )

    @abstractmethod
    def build_layers(self) -> dict[str, LayerBase]:
        """
        Return a dict of layer_name → LayerBase instances.

        ``initialise()`` runs every layer's ``initialise()`` in insertion order
        (so data layer prerequisites complete before, e.g., the services layer
        registers Ditto things). ``start()`` then launches every layer
        concurrently — the loops are long-running and have no in-order
        startup requirements. ``stop()`` runs in reverse insertion order.
        """
        ...

    async def initialise(self) -> None:
        self.layers = self.build_layers()
        for name, layer in self.layers.items():
            self.log.info("Initialising layer: %s", name)
            await layer.initialise()

    async def start(self) -> None:
        # All layer loops are independent and run concurrently. Insertion order
        # determines initialise() and stop() sequencing, not start() order.
        self._tasks = [
            asyncio.create_task(layer.start(), name=name)
            for name, layer in self.layers.items()
        ]
        # Supervise the loops rather than fire-and-forget them: if one layer's
        # start() raises, cancel the siblings so the twin doesn't limp along
        # half-alive with orphaned, unwatched tasks, then surface the error.
        # FIRST_EXCEPTION also returns once every loop has exited cleanly (the
        # normal graceful-stop path), so this does not cut healthy loops short.
        done, pending = await asyncio.wait(
            self._tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            # Cancellation is the expected graceful-stop path (stop() cancels
            # loops still parked in a long sleep) — not a failure to surface.
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                self.log.error("Layer '%s' failed: %s", task.get_name(), exc)
                raise exc

    async def stop(self) -> None:
        # Reverse insertion order: autonomous → intelligent → reactive → data.
        # This ensures no layer is shut down while a higher layer still depends on it.
        for layer in reversed(list(self.layers.values())):
            await layer.stop()
        # Each layer.stop() flips its _running flag, but a loop parked in a long
        # asyncio.sleep won't notice until it wakes. Cancel any still-running
        # task so shutdown is bounded by graceful-drain time, not by the longest
        # poll interval. Drain logic lives in stop(), which has already run.
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        # Release any pooled HTTP clients the connectors hold open.
        for conn in self.connectors:
            closer = getattr(conn, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as e:
                    self.log.warning("Connector close failed: %s", e)
        # Let in-flight event handlers finish their writes rather than being
        # killed when the loop closes; cancel only stragglers past the timeout.
        await self.bus.aclose()

    async def run(self) -> None:
        """Convenience: initialise then start."""
        await self.initialise()
        await self.start()
