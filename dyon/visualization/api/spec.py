"""``GET /api/viz/spec`` — the DashboardSpec the client renders, plus the
server's feature capabilities so the client can gate 3D/voice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from dyon.visualization.capabilities import capabilities

if TYPE_CHECKING:
    from dyon.visualization.context import VizContext


def build_spec_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.get("/spec")
    async def viz_spec():
        spec = ctx.spec_provider()
        return spec.model_dump()

    @router.get("/capabilities")
    async def viz_capabilities():
        return capabilities()

    return router
