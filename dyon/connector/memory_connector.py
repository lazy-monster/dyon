"""A connector for twins that share a process.

Collection twins reach their members through a connector: an aggregate polls
them, a composite pushes boundary values between them. Every connector in this
package speaks over a network — MQTT, Ditto's REST API, a generic HTTP API —
which means a composite whose members all live in one process still needs a
broker standing between two objects in the same interpreter.

:class:`InProcessConnector` closes that gap the same way
:class:`~dyon.services.ditto.memory.InProcessDittoClient` does, and against the
same :class:`~dyon.services.ditto.memory.ThingRegistry`, so a system can run its
members together with no infrastructure and the collection layer above them
cannot tell the difference:

::

    from dyon.connector import ConnectorRegistry, InProcessConnector
    from dyon.services.ditto.memory import shared_registry

    connectors = ConnectorRegistry(config)
    connectors.register(InProcessConnector(config, shared_registry()))

Its ``layer`` is ``"services"`` — the same layer the Ditto connector serves — so
a composite's ``query_component`` and boundary exchange find it by the route
they already look for. Swapping to a real deployment is then a matter of
registering ``DittoConnector`` instead, with nothing above it changing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from dyon.services.ditto.memory import ThingRegistry, shared_registry

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class InProcessConnector:
    """Cross-twin queries and pushes against an in-process Thing registry."""

    connector_type = "memory"
    layer = "services"

    def __init__(
        self,
        config: TwinConfig,
        registry: ThingRegistry | None = None,
        known_twins: list[str] | set[str] | None = None,
    ):
        """
        Parameters
        ----------
        config      : TwinConfig, read for the Ditto namespace that qualifies ids
        registry    : the shared Thing registry; defaults to the process-wide one
        known_twins : when given, ``can_reach`` answers True only for these ids.
                      Left ``None``, the connector claims any twin and the
                      registry decides on the actual call — matching how
                      ``DittoConnector`` behaves.
        """
        self._namespace = config.ditto.namespace
        self.registry = registry if registry is not None else shared_registry()
        self.known_twins = set(known_twins) if known_twins is not None else None

    def _thing_id(self, target_twin_id: str) -> str:
        # Accept either a bare asset id or an already-qualified Thing id.
        return (
            target_twin_id
            if ":" in target_twin_id
            else f"{self._namespace}:{target_twin_id}"
        )

    def can_reach(self, target_twin_id: str) -> bool:
        if self.known_twins is None:
            return bool(target_twin_id)
        return target_twin_id in self.known_twins

    async def query(self, target_twin_id: str, request: dict) -> dict:
        feature = request.get("feature", "telemetry")
        return self.registry.feature(self._thing_id(target_twin_id), feature)

    async def push(self, target_twin_id: str, data: dict) -> None:
        payload = dict(data)
        # Boundary values land in their own feature rather than the target's
        # telemetry, so a value carried in from a sibling is never mistaken for
        # something the twin measured itself.
        feature = payload.pop("_feature", "external_input")
        self.registry.update_feature(self._thing_id(target_twin_id), feature, payload)

    async def subscribe(
        self,
        target_twin_id: str,
        event_type: str,
        handler: Callable[[dict], Awaitable[None]],
    ) -> None:
        # Twins in one process share an EventBus; subscribe to that instead of
        # asking a state registry to deliver events it never sees.
        log.warning(
            "InProcessConnector.subscribe is not implemented — subscribe to the "
            "shared EventBus for in-process events"
        )

    async def aclose(self) -> None:
        return None


__all__ = ["InProcessConnector"]
