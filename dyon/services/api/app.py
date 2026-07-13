"""FastAPI app factory for the digital twin service layer."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.services.base import ServiceRegistry

log = logging.getLogger(__name__)


def create_app(
    config: TwinConfig,
    service_registry: ServiceRegistry,
    include_chat: bool = True,
    include_viz: bool = False,
    chat_agent=None,
) -> FastAPI:
    """Build and return the FastAPI application for this twin.

    ``include_viz`` is opt-in and off by default: with it false the app is
    exactly what it was before the visualization module existed. With it true,
    the ``/api/viz/*`` routes and the static dashboard are mounted via
    :func:`dyon.visualization.serve.mount_visualization`.

    ``chat_agent`` backs the single ``/api/chat`` endpoint the dashboard's chat
    panel posts to. Pass one — e.g. from
    :func:`~dyon.visualization.chat_agent.make_dashboard_chat_agent`, which adds
    the chart/forecast tools — to get a chat that can draw charts on request.
    Leave it ``None`` and the endpoint resolves the highest-priority agent of the
    registry's ``intelligent`` service per request, as before. The chat route is
    mounted exactly once, so ``include_chat`` and ``include_viz`` never collide.
    """
    from dyon import __version__
    from dyon.services.api.routes import build_router
    from dyon.services.api.streaming import build_chat_router

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        log.info("FastAPI server started for twin '%s'", config.asset_id)
        yield
        log.info("FastAPI server stopping for twin '%s'", config.asset_id)

    app = FastAPI(
        title=f"Digital Twin: {config.asset_name}",
        description=f"Dyon REST API for asset '{config.asset_id}'",
        version=__version__,
        lifespan=lifespan,
    )

    # Dev keeps the zero-config federation experience (any dashboard origin can
    # read this twin); production only answers origins explicitly listed.
    if config.security.mode == "production":
        origins = config.security.cors_origins
    else:
        origins = config.security.cors_origins or ["*"]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*", "x-api-key"],
        )

    if config.security.api_key:
        from dyon.services.api.auth import ApiKeyMiddleware
        app.add_middleware(ApiKeyMiddleware, api_key=config.security.api_key)

    app.include_router(build_router(config, service_registry))

    if include_chat:
        app.include_router(
            build_chat_router(config, service_registry, agent=chat_agent),
            prefix="/api",
        )

    if include_viz:
        from dyon.visualization.serve import mount_visualization

        # /api/chat is already served above when include_chat is on; only let the
        # visualization layer mount it (against chat_agent) when it is not, so the
        # route is bound exactly once.
        mount_visualization(
            app, config, service_registry,
            chat_agent=None if include_chat else chat_agent,
        )

    return app
