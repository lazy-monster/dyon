"""Versioned rule repository — stores reactive rules in PostgreSQL with hot-reload."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dyon.data.storage.postgres import PostgresAdapter

log = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS dt_rules (
    id          SERIAL PRIMARY KEY,
    asset_id    TEXT NOT NULL,
    rule_name   TEXT NOT NULL,
    rule_json   JSONB NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);
"""


@dataclass
class PersistedRule:
    rule_name: str
    condition_type: str       # "threshold" | "custom_fn_name"
    params: dict[str, Any]
    severity: str             # "warning" | "critical"
    version: int = 1
    is_active: bool = True


class _ThresholdRule:
    """A live Rule built from a persisted threshold definition.

    ``params`` = {"field": str, "direction": "high"|"low", "threshold": float};
    fires ``severity`` when the field crosses the threshold in that direction.
    """

    def __init__(self, rule_name: str, field: str, direction: str,
                 threshold: float, severity: str) -> None:
        self.rule_name = rule_name
        self._field = field
        self._low = direction == "low"
        self._threshold = threshold
        self._severity = severity

    def evaluate(self, readings: dict[str, float | None]) -> str | None:
        val = readings.get(self._field)
        if val is None:
            return None
        breached = (val < self._threshold) if self._low else (val > self._threshold)
        return self._severity if breached else None


class _FnRule:
    """A live Rule that delegates to a named callable from a registry."""

    def __init__(self, rule_name: str, fn, severity: str) -> None:
        self.rule_name = rule_name
        self._fn = fn
        self._severity = severity

    def evaluate(self, readings: dict[str, float | None]) -> str | None:
        return self._severity if self._fn(readings) else None


def rule_from_persisted(
    persisted: PersistedRule,
    fn_registry: dict[str, Any] | None = None,
):
    """Build a live ``Rule`` (see dyon.reactive.base) from a PersistedRule.

    ``threshold`` rules are self-contained. Any other ``condition_type`` is
    resolved as a predicate ``fn(readings) -> bool`` looked up by name in
    ``fn_registry`` — so domain code keeps ownership of bespoke logic while the
    repository owns activation/versioning.
    """
    if persisted.condition_type == "threshold":
        p = persisted.params
        return _ThresholdRule(
            persisted.rule_name, p["field"], p.get("direction", "high"),
            p["threshold"], persisted.severity,
        )
    if fn_registry and persisted.condition_type in fn_registry:
        return _FnRule(persisted.rule_name, fn_registry[persisted.condition_type],
                       persisted.severity)
    raise ValueError(
        f"Cannot build rule '{persisted.rule_name}': unknown condition_type "
        f"'{persisted.condition_type}' and no matching fn_registry entry."
    )


class RuleRepository:
    """
    PostgreSQL-backed rule store. Supports:
    - Adding/updating/deactivating rules
    - Hot-reload: callers poll load_active_rules() to get the latest set
    - Version audit trail
    """

    def __init__(self, pg: PostgresAdapter, asset_id: str) -> None:
        self._pg = pg
        self._asset_id = asset_id

    async def setup(self) -> None:
        await self._pg.execute(_CREATE_TABLE)
        log.info("RuleRepository ready for asset %s", self._asset_id)

    async def upsert(self, rule: PersistedRule) -> None:
        existing = await self._pg.fetchrow(
            "SELECT id, version FROM dt_rules WHERE asset_id=$1 AND rule_name=$2",
            self._asset_id, rule.rule_name,
        )
        now = time.time()
        if existing:
            await self._pg.execute(
                """UPDATE dt_rules
                   SET rule_json=$1, version=$2, is_active=$3, updated_at=$4
                   WHERE id=$5""",
                json.dumps({"condition_type": rule.condition_type,
                            "params": rule.params, "severity": rule.severity}),
                existing["version"] + 1,
                rule.is_active,
                now,
                existing["id"],
            )
        else:
            await self._pg.execute(
                """INSERT INTO dt_rules (asset_id, rule_name, rule_json, version,
                                        is_active, created_at, updated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                self._asset_id, rule.rule_name,
                json.dumps({"condition_type": rule.condition_type,
                            "params": rule.params, "severity": rule.severity}),
                rule.version, rule.is_active, now, now,
            )

    async def deactivate(self, rule_name: str) -> None:
        await self._pg.execute(
            "UPDATE dt_rules SET is_active=FALSE, updated_at=$1 WHERE asset_id=$2 AND rule_name=$3",
            time.time(), self._asset_id, rule_name,
        )

    async def load_active_rules(self) -> list[PersistedRule]:
        rows = await self._pg.fetch(
            "SELECT rule_name, rule_json, version FROM dt_rules "
            "WHERE asset_id=$1 AND is_active=TRUE ORDER BY rule_name",
            self._asset_id,
        )
        result = []
        for row in rows:
            raw = row["rule_json"]
            data = raw if isinstance(raw, dict) else json.loads(raw)
            result.append(PersistedRule(
                rule_name=row["rule_name"],
                condition_type=data["condition_type"],
                params=data["params"],
                severity=data["severity"],
                version=row["version"],
            ))
        return result

    async def history(self, rule_name: str) -> list[dict]:
        rows = await self._pg.fetch(
            "SELECT * FROM dt_rules WHERE asset_id=$1 AND rule_name=$2 ORDER BY version",
            self._asset_id, rule_name,
        )
        # Decode rule_json so callers get the same shape as load_active_rules
        # (asyncpg may return a JSON string for JSONB columns depending on the
        # codec configuration).
        for row in rows:
            raw = row.get("rule_json")
            if isinstance(raw, str):
                with contextlib.suppress(Exception):
                    row["rule_json"] = json.loads(raw)
        return rows
