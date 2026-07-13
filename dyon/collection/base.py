"""AbstractCollectionTwin: base for all collection-level digital twins."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from dyon.core.events import EventBus

if TYPE_CHECKING:
    from dyon.connector.base import ConnectorRegistry
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class AbstractCollectionTwin(ABC):
    """
    Base for all collection-level digital twins.

    A collection twin does not represent a physical asset directly.
    It aggregates, composes, or networks multiple component twins.
    """

    collection_type: str   # "aggregate" | "collection" | "composite" | "network"

    def __init__(
        self,
        collection_id: str,
        config: TwinConfig,
        component_twin_ids: list[str],
        connector_registry: ConnectorRegistry,
    ):
        self.collection_id = collection_id
        self.config = config
        self.component_ids = list(component_twin_ids)
        self.connectors = connector_registry
        self.bus = EventBus()
        self.log = logging.getLogger(
            f"dyon.collection.{self.collection_type}.{collection_id}"
        )
        self._running = False

    @abstractmethod
    async def aggregate_state(self) -> dict:
        """Gather state from all component twins."""
        ...

    @abstractmethod
    async def orchestrate(self) -> None:
        """One cycle of the orchestration loop."""
        ...

    async def query_component(
        self, twin_id: str, layer: str, request: dict
    ) -> dict:
        """Query a component twin through the connector registry."""
        conn = self.connectors.find_route(twin_id, layer)
        if conn is None:
            raise RuntimeError(
                f"No connector to reach '{twin_id}' at layer '{layer}'"
            )
        return await conn.query(twin_id, request)

    async def run(self, interval: int = 15) -> None:
        self._running = True
        self.log.info(
            "Collection twin '%s' started: %d components",
            self.collection_id,
            len(self.component_ids),
        )
        while self._running:
            try:
                await self.orchestrate()
            except Exception as e:
                self.log.error("Orchestration error: %s", e)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                self._running = False
                raise

    async def stop(self) -> None:
        """Request a graceful shutdown of the orchestration loop."""
        self._running = False
