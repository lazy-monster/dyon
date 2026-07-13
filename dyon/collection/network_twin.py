"""NetworkDT: connects asset twins through a typed relationship graph."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dyon.collection.base import AbstractCollectionTwin
from dyon.core.events import DomainEvent

if TYPE_CHECKING:
    from dyon.connector.base import ConnectorRegistry
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)

# Neo4j relationship types must be inlined into Cypher (parameter binding does
# not support label/relationship-type names), so we whitelist a safe character
# set rather than interpolating user input directly.
_VALID_REL_TYPE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass
class TwinRelationship:
    """A typed edge in the twin relationship graph."""

    source_twin: str
    target_twin: str
    relationship_type: str                   # "feeds", "supplies", "upstream_of", …
    properties: dict = field(default_factory=dict)


class NetworkDT(AbstractCollectionTwin):
    """
    Connects twins through a typed relationship graph.

    Provides:
    - Network-level KPIs (total throughput, bottleneck detection)
    - Cascade risk analysis (failure propagation)
    - Graph-based queries via Neo4j (optional)
    """

    collection_type = "network"

    def __init__(
        self,
        collection_id: str,
        config: TwinConfig,
        component_twin_ids: list[str],
        connector_registry: ConnectorRegistry,
        *,
        relationships: list[TwinRelationship],
        neo4j_driver=None,
    ):
        super().__init__(collection_id, config, component_twin_ids, connector_registry)
        self.relationships = relationships
        self.neo4j = neo4j_driver
        self._graph: dict[str, list[TwinRelationship]] = {}
        for rel in relationships:
            self._graph.setdefault(rel.source_twin, []).append(rel)

    async def initialise(self) -> None:
        await self.setup_network_graph()

    async def setup_network_graph(self) -> None:
        """Persist the twin network topology in Neo4j."""
        if not self.neo4j:
            return
        with self.neo4j.session() as session:
            for twin_id in self.component_ids:
                session.run("MERGE (t:DigitalTwin {id: $id})", id=twin_id)
            for rel in self.relationships:
                rel_type = rel.relationship_type.upper()
                if not _VALID_REL_TYPE.match(rel_type):
                    log.error(
                        "Skipping relationship '%s' → '%s': invalid type %r "
                        "(must match %s)",
                        rel.source_twin, rel.target_twin,
                        rel.relationship_type, _VALID_REL_TYPE.pattern,
                    )
                    continue
                session.run(
                    "MATCH (a:DigitalTwin {id: $src}), "
                    "(b:DigitalTwin {id: $tgt}) "
                    f"MERGE (a)-[:{rel_type}]->(b)",
                    src=rel.source_twin,
                    tgt=rel.target_twin,
                )

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

    async def detect_cascade_risk(self) -> list[dict]:
        """Identify twins whose failure could cascade through the network."""
        state = await self.aggregate_state()
        risks = []
        for twin_id, s in state.items():
            health = s.get("health", {}).get("health_score", 100.0)
            if isinstance(health, int | float) and health < 50.0:
                downstream = self._get_downstream(twin_id)
                if downstream:
                    risks.append(
                        {
                            "twin_id": twin_id,
                            "health": health,
                            "downstream_affected": downstream,
                            "cascade_severity": len(downstream),
                        }
                    )
        return sorted(risks, key=lambda r: -r["cascade_severity"])

    async def find_bottlenecks(self) -> list[str]:
        """Find twins that are single points of failure (articulation points)."""
        # An articulation point is only meaningful against a connected graph: if
        # the network is already disconnected, removing any node still leaves it
        # disconnected and every node would be falsely flagged. Report none.
        if not self._is_connected(self.component_ids):
            return []
        bottlenecks = []
        for twin_id in self.component_ids:
            remaining = [t for t in self.component_ids if t != twin_id]
            if not self._is_connected(remaining):
                bottlenecks.append(twin_id)
        return bottlenecks

    def _get_downstream(
        self, twin_id: str, visited: set | None = None
    ) -> list[str]:
        if visited is None:
            visited = set()
        visited.add(twin_id)
        downstream = []
        for rel in self._graph.get(twin_id, []):
            if rel.target_twin not in visited:
                downstream.append(rel.target_twin)
                downstream.extend(self._get_downstream(rel.target_twin, visited))
        return downstream

    def _is_connected(self, twin_ids: list[str]) -> bool:
        if not twin_ids:
            return True
        twin_set = set(twin_ids)
        visited: set[str] = set()
        queue = [twin_ids[0]]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for rel in self._graph.get(current, []):
                if rel.target_twin in twin_set:
                    queue.append(rel.target_twin)
            # Undirected traversal: check reverse edges too
            for src, rels in self._graph.items():
                if src in twin_set:
                    for rel in rels:
                        if rel.target_twin == current and src not in visited:
                            queue.append(src)
        return visited == twin_set

    async def orchestrate(self) -> None:
        state = await self.aggregate_state()

        healths = [
            s.get("health", {}).get("health_score", 100.0)
            for s in state.values()
            if "error" not in s
        ]
        network_health = sum(healths) / len(healths) if healths else 0.0
        self.log.info(
            "Network '%s' health: %.1f%%", self.collection_id, network_health
        )

        cascade_risks = await self.detect_cascade_risk()
        if cascade_risks:
            top = cascade_risks[0]
            self.log.warning(
                "Cascade risk: '%s' (health=%.1f) affects %d downstream twins",
                top["twin_id"],
                top["health"],
                top["cascade_severity"],
            )
            await self.bus.publish(
                DomainEvent(
                    event_type="network.cascade_risk",
                    source_layer="autonomous",
                    source_asset=self.collection_id,
                    payload=top,
                    severity="warning",
                )
            )

        bottlenecks = await self.find_bottlenecks()
        if bottlenecks:
            self.log.warning("Network bottlenecks detected: %s", bottlenecks)
