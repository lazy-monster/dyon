"""``POST /api/viz/chart`` — the non-agent direct path to a chart.

The dashboard (or any caller) posts an explicit ``{fields, window_minutes}`` or a
natural-language ``{query}``; the endpoint returns a :class:`ChartSpec` with a
complete inline Vega-Lite spec, built by the same helper the agent tool uses, so
both paths render identically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dyon.visualization.agent_tools import (
    build_timeseries_chart_spec,
    parse_chart_query,
)

if TYPE_CHECKING:
    from dyon.visualization.context import VizContext

log = logging.getLogger(__name__)


class ChartRequest(BaseModel):
    fields: list[str] = []
    query: str | None = None
    window_minutes: int = 120
    title: str | None = None


def build_chart_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.post("/chart")
    async def chart(req: ChartRequest):
        if ctx.ts_store is None:
            raise HTTPException(503, "No time-series store available")
        fields = req.fields
        if not fields and req.query:
            fields = parse_chart_query(ctx.config, req.query)
        if not fields:
            fields = ctx.config.field_names
        try:
            spec = build_timeseries_chart_spec(
                ctx.ts_store, ctx.config, fields, req.window_minutes, req.title,
            )
        except Exception:
            log.exception("chart build failed")
            raise HTTPException(500, "internal error") from None
        return spec.model_dump()

    return router
