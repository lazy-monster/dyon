"""Connector protocol and registry for cross-twin communication."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


@runtime_checkable
class ConnectorProtocol(Protocol):
    """Interface for cross-twin communication."""

    connector_type: str   # "mqtt" | "ditto" | "api"
    layer: str            # which DT layer this connector exposes

    def can_reach(self, target_twin_id: str) -> bool:
        """Check if this connector can communicate with the target twin."""
        ...

    async def query(self, target_twin_id: str, request: dict) -> dict:
        """Send a request to the target twin and get a response."""
        ...

    async def push(self, target_twin_id: str, data: dict) -> None:
        """Push data to the target twin (one-way)."""
        ...

    async def subscribe(
        self,
        target_twin_id: str,
        event_type: str,
        handler: Callable[[dict], Awaitable[None]],
    ) -> None:
        """Subscribe to events from the target twin."""
        ...


class ConnectorRegistry:
    """Manages all connectors for a twin and provides discovery."""

    def __init__(self, config: TwinConfig):
        self.config = config
        self._connectors: dict[str, list[ConnectorProtocol]] = {}

    def register(self, connector: ConnectorProtocol) -> None:
        self._connectors.setdefault(connector.layer, []).append(connector)
        log.debug(
            "Registered %s connector at layer '%s'",
            connector.connector_type,
            connector.layer,
        )

    def for_layer(self, layer: str) -> list[ConnectorProtocol]:
        return list(self._connectors.get(layer, []))

    def find_route(
        self, target_twin_id: str, layer: str
    ) -> ConnectorProtocol | None:
        """Find a connector that can reach the target at the specified layer."""
        for conn in self.for_layer(layer):
            if conn.can_reach(target_twin_id):
                return conn
        return None

    def all_connectors(self) -> list[ConnectorProtocol]:
        return [c for conns in self._connectors.values() for c in conns]

    async def aclose_all(self) -> None:
        """Close every connector that holds a reusable client (best-effort)."""
        for conn in self.all_connectors():
            closer = getattr(conn, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as e:
                    log.warning("Connector close failed: %s", e)
