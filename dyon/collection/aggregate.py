"""AggregateDT: fuses multiple asset twins into a single aggregate."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dyon.collection.base import AbstractCollectionTwin
from dyon.core.events import DomainEvent

if TYPE_CHECKING:
    from dyon.connector.base import ConnectorRegistry
    from dyon.core.config import TwinConfig
    from dyon.services.ditto.client import DittoClient

log = logging.getLogger(__name__)


class AggregateDT(AbstractCollectionTwin):
    """
    Combines multiple asset twins into a single aggregate with shared state.

    Maintains:
    - Merged telemetry (mean/min/max across all members)
    - Composite health score
    - Unified operational state (worst-case across members)
    - Shared control commands (broadcast to all members)
    """

    collection_type = "aggregate"

    def __init__(
        self,
        collection_id: str,
        config: TwinConfig,
        component_twin_ids: list[str],
        connector_registry: ConnectorRegistry,
        *,
        ditto_client: DittoClient,
    ):
        super().__init__(collection_id, config, component_twin_ids, connector_registry)
        self.ditto = ditto_client
        self._shared_state: dict = {}

    async def aggregate_state(self) -> dict:
        all_health: list[float] = []
        all_states: list[str] = []
        all_telemetry: dict[str, list[float]] = {}

        for twin_id in self.component_ids:
            try:
                health_data = await self.query_component(
                    twin_id, "services", {"feature": "health"}
                )
                all_health.append(health_data.get("health_score", 100.0))
                all_states.append(health_data.get("operational_state", "running"))

                telem = await self.query_component(
                    twin_id, "services", {"feature": "telemetry"}
                )
                for field, value in telem.items():
                    if isinstance(value, int | float):
                        all_telemetry.setdefault(field, []).append(float(value))
            except Exception as e:
                self.log.warning("Failed to reach '%s': %s", twin_id, e)

        self._shared_state = {
            "avg_health": sum(all_health) / len(all_health) if all_health else 100.0,
            "min_health": min(all_health) if all_health else 100.0,
            "worst_state": self._worst_state(all_states),
            "member_count": len(self.component_ids),
            "active_count": len(all_health),
            "telemetry_summary": {
                field: {
                    "mean": sum(vals) / len(vals),
                    "min": min(vals),
                    "max": max(vals),
                }
                for field, vals in all_telemetry.items()
                if vals
            },
        }
        return self._shared_state

    async def orchestrate(self) -> None:
        state = await self.aggregate_state()

        try:
            await self.ditto.update_feature(
                "fleet_health",
                {
                    "avg_health_score": state["avg_health"],
                    "min_health_score": state["min_health"],
                    "worst_state": state["worst_state"],
                    "active_members": state["active_count"],
                },
            )
        except Exception as e:
            self.log.warning("Ditto update failed: %s", e)

        if state["worst_state"] == "shutdown":
            await self.bus.publish(
                DomainEvent(
                    event_type="aggregate.critical",
                    source_layer="autonomous",
                    source_asset=self.collection_id,
                    payload=state,
                    severity="critical",
                )
            )

    async def broadcast_command(self, command: dict) -> dict[str, bool]:
        """Send the same control command to all member twins."""
        results: dict[str, bool] = {}
        for twin_id in self.component_ids:
            try:
                conn = self.connectors.find_route(twin_id, "services")
                if conn:
                    await conn.push(twin_id, command)
                    results[twin_id] = True
                else:
                    results[twin_id] = False
            except Exception:
                results[twin_id] = False
        return results

    @staticmethod
    def _worst_state(states: list[str]) -> str:
        order = {"running": 0, "warning": 1, "shutdown": 2}
        if not states:
            return "unknown"
        return max(states, key=lambda s: order.get(s, 0))
