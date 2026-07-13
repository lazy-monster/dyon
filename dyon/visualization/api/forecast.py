"""``GET /api/viz/forecast?field=x&steps=24`` — a forward projection for one
field, wrapping :class:`~dyon.simulation.forecaster.ProphetForecaster`.

Forecasting is an optional capability: if the backend (Prophet) is not
installed, the endpoint returns ``501`` and the client simply omits the forecast
overlay. The blocking fit/predict runs in a worker thread so the event loop is
never stalled.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from dyon.visualization.capabilities import capabilities

if TYPE_CHECKING:
    from dyon.visualization.context import VizContext

log = logging.getLogger(__name__)

_MAX_STEPS = 24 * 14   # two weeks of hourly steps


def _run_forecast(ts_store, field: str, steps: int) -> list[dict]:
    from dyon.simulation.forecaster import ProphetForecaster

    forecaster = ProphetForecaster(ts_store, field)
    forecaster.fit()
    return forecaster.predict(periods=steps, freq="h")


def build_forecast_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.get("/forecast")
    async def forecast(field: str, steps: int = 24):
        if not capabilities()["forecast"]:
            raise HTTPException(
                501, "Forecasting backend not installed (pip install 'dyon[forecast]')"
            )
        if ctx.ts_store is None:
            raise HTTPException(503, "No time-series store available")
        if field not in ctx.config.field_names:
            raise HTTPException(404, f"Unknown field '{field}'")
        steps = max(1, min(steps, _MAX_STEPS))
        try:
            series = await asyncio.to_thread(
                _run_forecast, ctx.ts_store, field, steps
            )
        except Exception:
            log.exception("forecast failed")
            raise HTTPException(500, "internal error") from None
        return {
            "field": field,
            "forecast": [
                {
                    "t": row["ds"].isoformat() if hasattr(row["ds"], "isoformat")
                    else row["ds"],
                    "v": row["yhat"],
                    "lower": row.get("yhat_lower"),
                    "upper": row.get("yhat_upper"),
                }
                for row in series
            ],
        }

    return router
