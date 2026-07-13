"""History and snapshot endpoints.

- ``GET /api/viz/history?fields=a,b&minutes=120`` → ``{field: [{t, v}]}``
- ``GET /api/viz/snapshot`` → latest value per field + current alarm states

Both read through the twin's :class:`TimeSeriesStore`. The lookback is clamped so
a client cannot ask for an unbounded scan.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from dyon.visualization.context import VizContext

log = logging.getLogger(__name__)

# Upper bound on a single history query (1 week). Mirrors the defensive clamping
# the reference dashboards applied to their own lookback windows.
_MAX_MINUTES = 7 * 24 * 60


def _normalize(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Map the store's ``{ts, value}`` points to the client contract ``{t, v}``."""
    out: dict[str, list[dict]] = {}
    for field, points in rows.items():
        out[field] = [
            {"t": p.get("ts", p.get("t")), "v": p.get("value", p.get("v"))}
            for p in points
        ]
    return out


def _alarm_state(value: float | None, rule: dict) -> bool:
    if value is None:
        return False
    if rule["direction"] == "below":
        return value < rule["threshold"]
    return value > rule["threshold"]


def build_history_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.get("/history")
    async def history(fields: str = "", minutes: int = 120):
        if ctx.ts_store is None:
            raise HTTPException(503, "No time-series store available")
        names = [f.strip() for f in fields.split(",") if f.strip()]
        if not names:
            names = ctx.config.field_names
        minutes = max(1, min(minutes, _MAX_MINUTES))
        try:
            rows = await ctx.ts_store.aquery_recent_fields(names, minutes=minutes)
        except Exception:
            log.exception("history query failed")
            raise HTTPException(500, "internal error") from None
        return _normalize(rows)

    @router.get("/snapshot")
    async def snapshot():
        if ctx.ts_store is None:
            raise HTTPException(503, "No time-series store available")
        names = ctx.config.field_names
        try:
            latest = await ctx.ts_store.aget_latest_fields(names)
        except Exception:
            log.exception("snapshot query failed")
            raise HTTPException(500, "internal error") from None

        spec = ctx.spec_provider()
        alarms: dict[str, list] = {"warn": [], "crit": []}
        for rule in spec.alarms:
            value = latest.get(rule.field)
            if _alarm_state(value, rule.model_dump()):
                alarms[rule.level].append(rule.field)

        return {"latest": latest, "alarms": alarms}

    return router
