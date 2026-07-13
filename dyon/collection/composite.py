"""CompositeDT: hierarchical composition with boundary condition exchange."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dyon.collection.base import AbstractCollectionTwin

if TYPE_CHECKING:
    from dyon.connector.base import ConnectorRegistry
    from dyon.core.config import TwinConfig
    from dyon.services.ditto.client import DittoClient

log = logging.getLogger(__name__)


@dataclass
class BoundaryCondition:
    """A data flow from one twin's output to another twin's input.

    ``transform`` controls how ``value`` is mapped before being pushed:
      - ``"direct"`` (default): pass through unchanged
      - ``"scale"``: multiply by ``transform_factor`` (default 1.0)
      - ``"offset"``: add ``transform_offset`` (default 0.0)
      - any callable: ``value -> transformed_value``
    """

    source_twin: str
    source_field: str          # output field in source twin
    target_twin: str
    target_field: str          # input field in target twin
    transform: str | Callable = "direct"
    transform_factor: float = 1.0    # used when transform == "scale"
    transform_offset: float = 0.0    # used when transform == "offset"


class CompositeDT(AbstractCollectionTwin):
    """
    Hierarchical composition with boundary condition exchange.

    Defines:
    - A hierarchy of sub-twins (parent-child relationships)
    - Boundary conditions: how output of one flows to input of another
    - Component swap: replace a sub-twin with another of the same type
    """

    collection_type = "composite"

    def __init__(
        self,
        collection_id: str,
        config: TwinConfig,
        component_twin_ids: list[str],
        connector_registry: ConnectorRegistry,
        *,
        hierarchy: dict[str, list[str]],
        boundary_conditions: list[BoundaryCondition],
        ditto_client: DittoClient,
    ):
        super().__init__(collection_id, config, component_twin_ids, connector_registry)
        self.hierarchy = hierarchy
        self.boundaries = boundary_conditions
        self.ditto = ditto_client

    async def aggregate_state(self) -> dict:
        state: dict[str, dict] = {}
        for twin_id in self.component_ids:
            try:
                telem = await self.query_component(
                    twin_id, "services", {"feature": "telemetry"}
                )
                health = await self.query_component(
                    twin_id, "services", {"feature": "health"}
                )
                state[twin_id] = {"telemetry": telem, "health": health}
            except Exception as e:
                state[twin_id] = {"error": str(e)}
        return state

    async def exchange_boundary_conditions(self) -> None:
        """Push outputs from source twins to target twins as inputs."""
        for bc in self.boundaries:
            try:
                source_data = await self.query_component(
                    bc.source_twin, "services", {"feature": "telemetry"}
                )
                value = source_data.get(bc.source_field)
                if value is None:
                    continue

                if callable(bc.transform):
                    target_value = bc.transform(value)
                elif bc.transform == "scale":
                    target_value = value * bc.transform_factor
                elif bc.transform == "offset":
                    target_value = value + bc.transform_offset
                else:
                    target_value = value   # "direct" or unknown → pass through

                conn = self.connectors.find_route(bc.target_twin, "data")
                if conn:
                    await conn.push(
                        bc.target_twin,
                        {bc.target_field: target_value, "_source": bc.source_twin},
                    )
            except Exception as e:
                self.log.error(
                    "Boundary exchange %s→%s failed: %s",
                    bc.source_twin,
                    bc.target_twin,
                    e,
                )

    async def swap_component(
        self, old_twin_id: str, new_twin_id: str,
        *, validate_interface: bool = True,
    ) -> None:
        """Replace a component twin with another of the same type.

        When ``validate_interface`` is True (default), the new twin must
        expose the same Ditto features (``telemetry``, ``health``) that the
        composite reads via :meth:`aggregate_state` and must produce every
        field referenced by boundary conditions where the old twin is the
        source. Validation failure raises ``ValueError`` and leaves the
        composite untouched.

        Pass ``validate_interface=False`` to skip the check when the new twin
        is being introduced before its Ditto thing has been published (e.g.
        replaying a recorded swap, or staging a swap before the twin boots).
        """
        if old_twin_id not in self.component_ids:
            raise ValueError(f"'{old_twin_id}' is not a component of this composite")

        if validate_interface:
            try:
                new_telemetry = await self.query_component(
                    new_twin_id, "services", {"feature": "telemetry"}
                )
                await self.query_component(
                    new_twin_id, "services", {"feature": "health"}
                )
            except Exception as e:
                raise ValueError(
                    f"Cannot swap to '{new_twin_id}': required Ditto features not reachable: {e}"
                ) from e

            required_outputs = {
                bc.source_field
                for bc in self.boundaries
                if bc.source_twin == old_twin_id
            }
            missing = required_outputs - set(new_telemetry.keys())
            if missing:
                raise ValueError(
                    f"Cannot swap to '{new_twin_id}': missing required output "
                    f"fields {sorted(missing)}"
                )

        idx = self.component_ids.index(old_twin_id)
        self.component_ids[idx] = new_twin_id

        for bc in self.boundaries:
            if bc.source_twin == old_twin_id:
                bc.source_twin = new_twin_id
            if bc.target_twin == old_twin_id:
                bc.target_twin = new_twin_id

        for _parent, children in self.hierarchy.items():
            if old_twin_id in children:
                children[children.index(old_twin_id)] = new_twin_id
        if old_twin_id in self.hierarchy:
            self.hierarchy[new_twin_id] = self.hierarchy.pop(old_twin_id)

        self.log.info("Swapped component: %s → %s", old_twin_id, new_twin_id)

    async def orchestrate(self) -> None:
        await self.exchange_boundary_conditions()
        state = await self.aggregate_state()

        for parent, children in self.hierarchy.items():
            child_healths = [
                state.get(c, {}).get("health", {}).get("health_score", 100.0)
                for c in children
            ]
            if child_healths:
                composite_health = min(child_healths)
                try:
                    await self.ditto.update_feature(
                        f"composite_{parent}_health",
                        {"health_score": composite_health},
                    )
                except Exception as e:
                    self.log.warning("Ditto update failed for parent '%s': %s", parent, e)
