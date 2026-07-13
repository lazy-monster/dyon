"""Schema-driven Neo4j knowledge graph for asset diagnostics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


@dataclass
class FailureMode:
    name: str
    severity: str                        # "low" | "medium" | "high" | "critical"
    maintenance_actions: list[str]
    affected_components: list[str] = field(default_factory=list)


@dataclass
class SymptomMapping:
    symptom_name: str
    sensor_field: str
    threshold: float
    failure_modes: list[str]
    direction: str = "high"              # "high" = triggers above threshold


@dataclass
class KnowledgeGraphSpec:
    components: list[str]
    failure_modes: list[FailureMode]
    symptom_mappings: list[SymptomMapping]
    # Optional raw Cypher statements appended after the standard schema setup.
    # Use for domain-specific node types (Product, Category, PitchStrategy, …).
    custom_cypher: list[str] = field(default_factory=list)

    def to_cypher(self, asset_id: str, asset_type: str) -> list[tuple[str, dict]]:
        """Return a list of (cypher_statement, parameters) pairs.

        Parameter binding (instead of string interpolation) prevents Cypher
        injection when symbol names contain quotes or backslashes, and is the
        recommended Neo4j driver pattern.
        """
        stmts: list[tuple[str, dict]] = []

        # Asset node
        stmts.append((
            "MERGE (a:Asset {id: $asset_id}) SET a.type = $asset_type",
            {"asset_id": asset_id, "asset_type": asset_type},
        ))

        # Component nodes
        for comp in self.components:
            stmts.append((
                "MERGE (c:Component {name: $name})",
                {"name": comp},
            ))
            stmts.append((
                "MATCH (a:Asset {id: $asset_id}), (c:Component {name: $name}) "
                "MERGE (a)-[:HAS_COMPONENT]->(c)",
                {"asset_id": asset_id, "name": comp},
            ))

        # Failure modes + maintenance actions
        for fm in self.failure_modes:
            stmts.append((
                "MERGE (f:FailureMode {name: $name}) SET f.severity = $severity",
                {"name": fm.name, "severity": fm.severity},
            ))
            for action in fm.maintenance_actions:
                stmts.append((
                    "MERGE (m:MaintenanceAction {name: $name})",
                    {"name": action},
                ))
                stmts.append((
                    "MATCH (f:FailureMode {name: $fm_name}), "
                    "(m:MaintenanceAction {name: $action}) "
                    "MERGE (f)-[:REQUIRES]->(m)",
                    {"fm_name": fm.name, "action": action},
                ))
            for comp in fm.affected_components:
                stmts.append((
                    "MATCH (c:Component {name: $comp}), "
                    "(f:FailureMode {name: $fm_name}) "
                    "MERGE (c)-[:CAN_HAVE]->(f)",
                    {"comp": comp, "fm_name": fm.name},
                ))

        # Symptom mappings
        for sm in self.symptom_mappings:
            stmts.append((
                "MERGE (sy:Symptom {name: $name}) "
                "SET sy.field = $field, sy.threshold = $threshold, "
                "    sy.direction = $direction",
                {
                    "name": sm.symptom_name,
                    "field": sm.sensor_field,
                    "threshold": float(sm.threshold),
                    "direction": sm.direction,
                },
            ))
            for fm_name in sm.failure_modes:
                stmts.append((
                    "MATCH (f:FailureMode {name: $fm_name}), "
                    "(sy:Symptom {name: $sym_name}) "
                    "MERGE (f)-[:CAUSES]->(sy)",
                    {"fm_name": fm_name, "sym_name": sm.symptom_name},
                ))

        # Domain-specific custom Cypher (e.g. Product, Category, PitchStrategy nodes).
        # Custom statements are caller-authored, so they remain plain strings —
        # we cannot infer their parameters.
        for stmt in self.custom_cypher:
            stmts.append((stmt, {}))

        return stmts


class KnowledgeGraph:
    """Schema-driven Neo4j knowledge graph for asset diagnostics."""

    def __init__(self, config: TwinConfig, driver):
        # Guard against the easy mistake of passing the spec here (the second
        # argument is a Neo4j driver). Without this, setup_from_spec() is never
        # called, _spec stays None, and every diagnose/get_components call fails
        # silently through the defensive except blocks below.
        if isinstance(driver, KnowledgeGraphSpec):
            raise TypeError(
                "KnowledgeGraph(config, driver) expects a Neo4j driver as the "
                "second argument, not a KnowledgeGraphSpec. Build a driver with "
                "neo4j.GraphDatabase.driver(uri, auth=(user, password)) and pass "
                "the spec to setup_from_spec() instead."
            )
        if not hasattr(driver, "session"):
            raise TypeError(
                "KnowledgeGraph expects a Neo4j driver exposing .session(), got "
                f"{type(driver).__name__}."
            )
        self.config = config
        self.driver = driver
        self._spec: KnowledgeGraphSpec | None = None

    def setup_from_spec(self, spec: KnowledgeGraphSpec) -> None:
        self._spec = spec
        with self.driver.session() as session:
            for stmt, params in spec.to_cypher(
                self.config.asset_id, self.config.asset_type
            ):
                try:
                    session.run(stmt, **params)
                except Exception as e:
                    log.warning("KG Cypher error: %s | stmt: %s", e, stmt[:80])
        log.info(
            "Knowledge graph built: %d components, %d failure modes",
            len(spec.components),
            len(spec.failure_modes),
        )

    def diagnose_from_readings(self, readings: dict[str, float]) -> list[str]:
        """Return symptom names triggered by current readings."""
        if self._spec is None:
            return []
        active = []
        for sm in self._spec.symptom_mappings:
            val = readings.get(sm.sensor_field)
            if val is None:
                continue
            triggered = (
                (sm.direction == "high" and val > sm.threshold)
                or (sm.direction == "low" and val < sm.threshold)
            )
            if triggered:
                active.append(sm.symptom_name)
        return active

    def diagnose(self, symptoms: list[str]) -> list[dict]:
        """Query failure modes from active symptoms via Neo4j."""
        if not symptoms:
            return []
        # Sort by an explicit severity rank so 'critical' > 'high' > 'medium'
        # > 'low'. Alphabetical ordering on severity strings is wrong.
        cypher = """
        MATCH (sy:Symptom)<-[:CAUSES]-(f:FailureMode)-[:REQUIRES]->(m:MaintenanceAction)
        WHERE sy.name IN $symptoms
        WITH f, collect(DISTINCT m.name) AS actions,
             CASE f.severity
                 WHEN 'critical' THEN 4
                 WHEN 'high'     THEN 3
                 WHEN 'medium'   THEN 2
                 WHEN 'low'      THEN 1
                 ELSE 0
             END AS rank
        RETURN f.name AS failure, f.severity AS severity, actions
        ORDER BY rank DESC
        """
        try:
            with self.driver.session() as session:
                return [dict(r) for r in session.run(cypher, symptoms=symptoms)]
        except Exception as e:
            log.error("KG diagnose error: %s", e)
            return []

    def get_components(self) -> list[str]:
        cypher = """
        MATCH (:Asset {id: $aid})-[:HAS_COMPONENT]->(c:Component)
        RETURN c.name AS name
        """
        try:
            with self.driver.session() as session:
                return [r["name"] for r in session.run(cypher, aid=self.config.asset_id)]
        except Exception as e:
            log.error("KG get_components error: %s", e)
            return []

    def close(self) -> None:
        self.driver.close()
