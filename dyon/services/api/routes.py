"""Default REST routes for the digital twin API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.services.base import ServiceRegistry

log = logging.getLogger(__name__)


def build_router(
    config: TwinConfig, service_registry: ServiceRegistry
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        from dyon.core import metrics

        return {
            "asset_id": config.asset_id,
            "status": "ok",
            "counters": metrics.snapshot(),
        }

    @router.get("/api/twin/state")
    async def twin_state():
        try:
            ditto_svc: Any = service_registry.get("ditto_sync")
            return await ditto_svc.ditto.get_thing()
        except KeyError:
            raise HTTPException(404, "Ditto sync service not registered") from None
        except Exception:
            log.exception("twin_state failed")
            raise HTTPException(500, "internal error") from None

    @router.get("/api/twin/telemetry")
    async def twin_telemetry():
        try:
            ditto_svc: Any = service_registry.get("ditto_sync")
            return await ditto_svc.ditto.get_feature("telemetry")
        except Exception:
            log.exception("twin_telemetry failed")
            raise HTTPException(500, "internal error") from None

    @router.get("/api/twin/health-score")
    async def twin_health_score():
        try:
            ditto_svc: Any = service_registry.get("ditto_sync")
            return await ditto_svc.ditto.get_feature("health")
        except Exception:
            log.exception("twin_health_score failed")
            raise HTTPException(500, "internal error") from None

    @router.post("/api/twin/external")
    async def twin_external(request: Request):
        """Receive a push from another twin's APIConnector.

        Looks up the registered ``data`` service and forwards the payload to
        its ``route()`` method if present. Returns 503 if no data router is
        registered under the standard name ``"data"``.
        """
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body") from None
        try:
            data_svc = service_registry.get("data")
        except KeyError:
            raise HTTPException(503, "Data router not registered as 'data'") from None
        if not hasattr(data_svc, "route"):
            raise HTTPException(503, "Registered 'data' service does not accept routed input")
        try:
            await data_svc.route(payload)
            return {"accepted": True}
        except Exception:
            log.exception("twin_external route failed")
            raise HTTPException(500, "internal error") from None

    @router.get("/api/twin/events")
    async def twin_events(n: int = 20):
        try:
            ditto_svc: Any = service_registry.get("ditto_sync")
            if ditto_svc.doc is None:
                raise HTTPException(
                    503,
                    "Events endpoint requires doc_store to be passed to DittoSyncService",
                )
            events = ditto_svc.doc.get_recent_events(n)
            return {"events": events}
        except KeyError:
            raise HTTPException(404, "Ditto sync service not registered") from None
        except HTTPException:
            raise
        except Exception:
            log.exception("twin_events failed")
            raise HTTPException(500, "internal error") from None

    return router
