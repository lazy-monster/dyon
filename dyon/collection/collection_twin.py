"""CollectionDT: groups twins of the same type for collective monitoring."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dyon.collection.base import AbstractCollectionTwin

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class CollectionDT(AbstractCollectionTwin):
    """
    Groups twins of the same type for collective monitoring and batch ops.

    Key capabilities:
    - Batch health queries across all members
    - Outlier detection (which twins deviate from the group?)
    - Batch commands (send same control to all members)
    - Statistical comparisons (ranking, percentiles)
    """

    collection_type = "collection"

    async def aggregate_state(self) -> dict:
        """Collect individual states while preserving identity."""
        member_states: dict = {}
        for twin_id in self.component_ids:
            try:
                health = await self.query_component(
                    twin_id, "services", {"feature": "health"}
                )
                telem = await self.query_component(
                    twin_id, "services", {"feature": "telemetry"}
                )
                member_states[twin_id] = {"health": health, "telemetry": telem}
            except Exception as e:
                member_states[twin_id] = {"error": str(e)}
        return member_states

    async def find_outliers(
        self, field: str, z_threshold: float = 2.0
    ) -> list[str]:
        """Find twins whose field value deviates from the group mean."""
        states = await self.aggregate_state()
        values: dict[str, float] = {}
        for tid, s in states.items():
            telem = s.get("telemetry", {})
            val = telem.get(field)
            if isinstance(val, int | float):
                values[tid] = float(val)

        if len(values) < 3:
            return []

        mean = sum(values.values()) / len(values)
        variance = sum((v - mean) ** 2 for v in values.values()) / len(values)
        std = variance ** 0.5
        if std == 0:
            return []

        return [
            tid for tid, v in values.items() if abs(v - mean) / std > z_threshold
        ]

    async def batch_command(self, command: dict) -> dict[str, bool]:
        """Send the same command to all members. Returns success map."""
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

    async def rank_by_health(self) -> list[tuple[str, float]]:
        """Return members ranked worst-to-best by health score."""
        states = await self.aggregate_state()
        ranked = []
        for tid, s in states.items():
            score = s.get("health", {}).get("health_score", 100.0)
            ranked.append((tid, float(score)))
        return sorted(ranked, key=lambda x: x[1])

    async def orchestrate(self) -> None:
        states = await self.aggregate_state()
        healthy = sum(
            1
            for s in states.values()
            if s.get("health", {}).get("operational_state") == "running"
        )
        self.log.info(
            "Collection '%s': %d/%d members healthy",
            self.collection_id,
            healthy,
            len(states),
        )
