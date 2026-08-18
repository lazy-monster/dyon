"""Cross-twin state exchange without a Ditto server.

Twins read each other through :class:`~dyon.services.ditto.client.DittoClient`:
a twin writes its own state to its Thing and reads a sibling's state from
theirs. Eclipse Ditto is the right home for that when the twins are separate
deployments — but a composite that runs its members in a single process pays a
whole broker, and a whole container, to move a dictionary between two objects.

:class:`InProcessDittoClient` is that same contract backed by a shared registry
of Things held in memory. Give every twin in the process a client onto the same
:class:`ThingRegistry` and cross-twin reads work exactly as they do against a
server, with no server:

::

    from dyon.services.ditto.memory import InProcessDittoClient, ThingRegistry

    registry = ThingRegistry()
    pump = InProcessDittoClient(pump_config, registry)
    controller = InProcessDittoClient(controller_config, registry)

    await pump.update_feature("telemetry", {"flow_rate": 0.8})
    await controller.get_thing_feature(pump_thing_id, "telemetry")

The registry is deliberately explicit rather than global: a test that builds two
systems must be able to keep their Things apart. :func:`shared_registry` exists
for the common case where a single process really does host one system.

What this does not do is persist, authorise, notify, or federate. A deployment
whose twins live in different processes or on different machines wants the real
client and a real Ditto.
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class ThingNotFoundError(KeyError):
    """Raised when a read targets a Thing or feature that does not exist.

    Mirrors the ``raise_for_status`` failure the HTTP client produces on a 404,
    because callers already handle that as "the companion twin is not running"
    and fall back to their own defaults.
    """


class ThingRegistry:
    """The Things known to one process, keyed by Thing id."""

    def __init__(self) -> None:
        self._things: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, thing_id: str, payload: dict) -> None:
        with self._lock:
            existing = self._things.get(thing_id)
            if existing is None:
                self._things[thing_id] = copy.deepcopy(payload)
                return
            # Re-registering must not wipe live telemetry: a twin restarting its
            # services layer would otherwise blank the state its siblings read.
            existing.setdefault("features", {})
            for feature, body in (payload.get("features") or {}).items():
                existing["features"].setdefault(feature, body)
            existing["attributes"] = payload.get("attributes", existing.get("attributes", {}))

    def get(self, thing_id: str) -> dict:
        with self._lock:
            thing = self._things.get(thing_id)
            if thing is None:
                raise ThingNotFoundError(f"No Thing '{thing_id}' in this process")
            return copy.deepcopy(thing)

    def feature(self, thing_id: str, feature: str) -> dict:
        with self._lock:
            thing = self._things.get(thing_id)
            if thing is None:
                raise ThingNotFoundError(f"No Thing '{thing_id}' in this process")
            body = (thing.get("features") or {}).get(feature)
            if body is None:
                raise ThingNotFoundError(
                    f"Thing '{thing_id}' has no feature '{feature}'"
                )
            return copy.deepcopy(body.get("properties", {}))

    def update_feature(self, thing_id: str, feature: str, properties: dict) -> None:
        """Merge ``properties`` into a feature, creating the Thing if needed.

        Creating on write matches how composite twins actually behave: one twin
        writes an observation into a sibling's Thing, and that write must land
        whether or not the sibling has registered yet.
        """
        with self._lock:
            thing = self._things.setdefault(thing_id, {"attributes": {}, "features": {}})
            features = thing.setdefault("features", {})
            body = features.setdefault(feature, {"properties": {}})
            body.setdefault("properties", {}).update(copy.deepcopy(properties))

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._things)

    def snapshot(self) -> dict[str, dict]:
        """Every Thing, deep-copied — the whole system's state in one object."""
        with self._lock:
            return copy.deepcopy(self._things)

    def clear(self) -> None:
        with self._lock:
            self._things.clear()


_SHARED = ThingRegistry()


def shared_registry() -> ThingRegistry:
    """The process-wide registry, for the common single-system case."""
    return _SHARED


class InProcessDittoClient:
    """A :class:`DittoClient`-shaped client backed by a :class:`ThingRegistry`.

    Every method the framework and its twins call on the HTTP client is
    implemented here with the same signature and the same failure behaviour, so
    a twin cannot tell which one it was given.
    """

    def __init__(
        self, config: TwinConfig, registry: ThingRegistry | None = None
    ) -> None:
        self._cfg = config.ditto
        self._config = config
        self._asset_id = config.asset_id
        self._thing_id = config.thing_id
        self.registry = registry if registry is not None else shared_registry()

    @property
    def thing_id(self) -> str:
        return self._thing_id

    async def wait_for_ready(self, timeout: int = 120) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def create_policy(self) -> None:
        # Authorisation is meaningless inside one process; accepted so that a
        # services layer written against the HTTP client needs no branch.
        return None

    async def create_thing(self, config: TwinConfig | None = None) -> None:
        cfg = config or self._config
        self.registry.create(
            self._thing_id,
            {
                "thingId": self._thing_id,
                "attributes": {
                    "asset_id":   cfg.asset_id,
                    "asset_type": cfg.asset_type,
                    "asset_name": cfg.asset_name,
                },
                "features": {
                    "telemetry": {"properties": {}},
                    "health": {
                        "properties": {
                            "health_score": 100.0,
                            "operational_state": "running",
                        }
                    },
                },
            },
        )
        log.info("In-process Thing '%s' ready", self._thing_id)

    async def get_thing(self) -> dict:
        return self.registry.get(self._thing_id)

    async def update_feature(self, feature: str, properties: dict) -> None:
        self.registry.update_feature(self._thing_id, feature, properties)

    async def get_feature(self, feature: str) -> dict:
        return self.registry.feature(self._thing_id, feature)

    async def get_thing_feature(self, thing_id: str, feature: str) -> dict:
        return self.registry.feature(thing_id, feature)

    async def update_thing_feature(
        self, thing_id: str, feature: str, properties: dict
    ) -> None:
        self.registry.update_feature(thing_id, feature, properties)

    def __repr__(self) -> str:
        return f"InProcessDittoClient(thing_id={self._thing_id!r})"


def things_snapshot(registry: ThingRegistry | None = None) -> dict[str, Any]:
    """Every Thing in a registry — useful for a dashboard or a system probe."""
    return (registry or shared_registry()).snapshot()


__all__ = [
    "InProcessDittoClient",
    "ThingNotFoundError",
    "ThingRegistry",
    "shared_registry",
    "things_snapshot",
]
