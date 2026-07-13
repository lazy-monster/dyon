"""Wiring: attach the visualization API and the dashboard client to a FastAPI
app, or build a standalone app for it.

``mount_visualization`` is the one-line opt-in: call it on any existing app and
the ``/api/viz/*`` routes — and, by default, the static dashboard — appear. Do
nothing and the app is unchanged. Stores and the event bus are discovered from
the :class:`ServiceRegistry` when not passed explicitly, so a fully wired twin
needs no extra arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi.staticfiles import StaticFiles

from dyon.visualization.api.agents import build_agents_router
from dyon.visualization.api.chart import build_chart_router
from dyon.visualization.api.forecast import build_forecast_router
from dyon.visualization.api.history import build_history_router
from dyon.visualization.api.live import build_live_router
from dyon.visualization.api.spec import build_spec_router
from dyon.visualization.api.voice import build_voice_router
from dyon.visualization.context import VizContext
from dyon.visualization.derive import derive_default_spec

if TYPE_CHECKING:
    from fastapi import FastAPI

    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import DocumentStore, TimeSeriesStore
    from dyon.services.base import ServiceRegistry
    from dyon.visualization.schema import DashboardSpec

VIZ_PREFIX = "/api/viz"


def _find_attr(registry: ServiceRegistry, *names: str):
    """Return the first service attribute matching one of ``names``.

    Stores are not registered as services in their own right; they hang off the
    services that use them (e.g. the ``data`` router's ``.ts`` / ``.doc``). This
    scans registered services for the first matching attribute.
    """
    for service in registry.all().values():
        for name in names:
            value = getattr(service, name, None)
            if value is not None:
                return value
    return None


def _resolve_event_bus(registry: ServiceRegistry):
    return _find_attr(registry, "bus", "event_bus", "_bus")


def _cors_origins(config: TwinConfig | None) -> list[str]:
    """Resolve allowed CORS origins from a twin config.

    Dev mode (or no config) keeps the zero-config federation experience — any
    dashboard origin can read this twin. Production answers only the origins
    explicitly listed in ``security.cors_origins``.
    """
    if config is None:
        return ["*"]
    if config.security.mode == "production":
        return list(config.security.cors_origins)
    return list(config.security.cors_origins) or ["*"]


def _ensure_cors(app: FastAPI, config: TwinConfig | None = None) -> None:
    """Open CORS on ``app`` unless a CORS middleware is already installed.

    A combined dashboard runs on its own origin (a different port) and pulls each
    member twin's ``/api/viz/*`` data straight from the browser, so every twin
    that serves a dashboard must answer cross-origin requests. In dev mode that
    means any origin; in production only the origins listed in the security
    config. Idempotent, so an app that already installed CORS is left untouched.
    """
    from starlette.middleware.cors import CORSMiddleware

    if any(mw.cls is CORSMiddleware for mw in app.user_middleware):
        return
    origins = _cors_origins(config)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*", "x-api-key"],
        )


def _ensure_api_key(app: FastAPI, config: TwinConfig | None) -> None:
    """Install the API-key middleware when one is configured, idempotently.

    ``mount_visualization`` may be called on a user's own app that never went
    through :func:`~dyon.services.api.app.create_app`, so the guard is applied
    here too. Skipped when the key is empty (dev mode) or already installed.
    """
    if config is None or not config.security.api_key:
        return
    from dyon.services.api.auth import ApiKeyMiddleware

    if any(mw.cls is ApiKeyMiddleware for mw in app.user_middleware):
        return
    app.add_middleware(ApiKeyMiddleware, api_key=config.security.api_key)


def _resolve_mas(registry: ServiceRegistry):
    """Find the multi-agent system on the registry (registered as ``intelligent``).

    Returns ``None`` when there is no registry or no such service, so the agents
    endpoint simply reports nothing rather than failing.
    """
    if registry is None:
        return None
    try:
        return registry.get("intelligent")
    except (KeyError, AttributeError):
        return None


def mount_visualization(
    app: FastAPI,
    config: TwinConfig,
    service_registry: ServiceRegistry,
    *,
    spec: DashboardSpec | None = None,
    spec_provider: Callable[[], DashboardSpec] | None = None,
    event_bus: EventBus | None = None,
    ts_store: TimeSeriesStore | None = None,
    doc_store: DocumentStore | None = None,
    chat_agent=None,
    mas=None,
    serve_dashboard: bool = True,
    base_path: str = "",
) -> FastAPI:
    """Attach the visualization routers to ``app`` under ``/api/viz``.

    ``spec`` pins a fixed dashboard; ``spec_provider`` supplies one lazily per
    request; if neither is given, the dashboard is derived from ``config`` on
    each request. ``event_bus``/``ts_store``/``doc_store`` default to whatever is
    discoverable on the service registry.

    ``chat_agent`` backs the "Ask the Twin" panel: pass one (e.g. from
    :func:`~dyon.visualization.chat_agent.make_dashboard_chat_agent`) and a
    ``POST /api/chat`` route is mounted against it. Leave it ``None`` to not
    mount chat here (e.g. when the host app already serves ``/api/chat``).

    ``base_path`` mounts everything under a prefix (``/boiler`` →
    ``/boiler/api/viz/*``, ``/boiler/api/chat``, ``/boiler/dashboard``). This lets several
    twins share one app on a single origin — the way a bundled composite twin
    serves its own dashboard at the root while exposing each member twin under
    its own path for the browser to federate. Leave it empty for a lone twin.
    """
    # Make the twin federation-ready: a combined dashboard on another origin
    # reads these routes from the browser.
    _ensure_cors(app, config)
    _ensure_api_key(app, config)

    if spec_provider is None:
        if spec is not None:
            spec_provider = lambda: spec  # noqa: E731 - trivial constant provider
        else:
            spec_provider = lambda: derive_default_spec(config)  # noqa: E731

    ctx = VizContext(
        config=config,
        service_registry=service_registry,
        spec_provider=spec_provider,
        event_bus=event_bus or _resolve_event_bus(service_registry),
        ts_store=ts_store or _find_attr(service_registry, "ts", "_ts_store", "ts_store"),
        doc_store=doc_store or _find_attr(service_registry, "doc", "_doc_store", "doc_store"),
        mas=mas if mas is not None else _resolve_mas(service_registry),
    )

    viz_prefix = base_path + VIZ_PREFIX
    app.include_router(build_spec_router(ctx), prefix=viz_prefix)
    app.include_router(build_history_router(ctx), prefix=viz_prefix)
    app.include_router(build_forecast_router(ctx), prefix=viz_prefix)
    app.include_router(build_chart_router(ctx), prefix=viz_prefix)
    app.include_router(build_voice_router(ctx), prefix=viz_prefix)
    app.include_router(build_live_router(ctx), prefix=viz_prefix)
    app.include_router(build_agents_router(ctx), prefix=viz_prefix)

    # The conversational panel posts to /api/chat; mount it against the supplied
    # chat agent. Without one, the panel degrades gracefully in the client.
    if chat_agent is not None:
        from dyon.services.api.streaming import build_chat_router

        app.include_router(build_chat_router(config, agent=chat_agent), prefix=base_path + "/api")

    # Serve the framework-owned static dashboard at <base_path>/dashboard. Pass
    # serve_dashboard=False when the host app serves its own client instead.
    if serve_dashboard:
        _mount_dashboard_assets(app, base_path + "/dashboard")

    return app


class _RevalidatingStatic(StaticFiles):
    """Serve dashboard assets with ``Cache-Control: no-cache`` so the browser
    revalidates via ETag on every load instead of holding a stale copy. The
    files are tiny and carry ETags, so this costs a cheap 304 when unchanged
    while guaranteeing edits (and query-param-driven behaviour) take effect on
    reload rather than being masked by heuristic caching."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_dashboard_assets(app: FastAPI, path: str = "/dashboard") -> None:
    """Mount the framework-owned static dashboard at ``path``. A no-op when the
    assets directory is missing, so mounting the API alone still works."""
    import os

    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    if os.path.isdir(assets_dir) and os.listdir(assets_dir):
        app.mount(
            path,
            _RevalidatingStatic(directory=assets_dir, html=True),
            name="dyon-dashboard" + (path if path != "/dashboard" else ""),
        )


def create_combined_dashboard_app(
    spec: DashboardSpec,
    *,
    chat_agent=None,
    mas=None,
    event_bus: EventBus | None = None,
    ts_store: TimeSeriesStore | None = None,
    config: TwinConfig | None = None,
) -> FastAPI:
    """Build a standalone app for a *combined* twin's federated dashboard.

    By default a combined dashboard owns no stores: its overview is rendered
    client-side from ``spec.combination`` + ``spec.members``/topology, and every
    member's panels and live data are pulled from that member's own ``api_base``.
    So this serves the dashboard spec, capabilities, the static client, and —
    optionally — a chat endpoint for a roll-up assistant. Build ``spec`` with
    :func:`~dyon.visualization.derive.derive_combined_spec` or
    :func:`~dyon.visualization.derive.combined_spec_from_twin`.

    When the combined twin is a *real* twin with its own agents — a composite
    overseer that supervises its members, say — pass its ``mas`` (and optionally
    ``event_bus``/``ts_store``/``config``). The combined dashboard then also
    serves ``/api/viz/agents`` (and live/history) for the composite's own
    multi-agent system, so the overview's Agents tab shows the overseer in
    action alongside the members.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title=f"Dyon Dashboard: {spec.asset_name}")
    origins = _cors_origins(config)
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins,
            allow_methods=["*"], allow_headers=["*", "x-api-key"],
        )
    _ensure_api_key(app, config)

    # A combined dashboard owns no config/registry of its own — its overview is
    # client-rendered and members are federated from their own api_base — so both
    # are intentionally absent here (the routers this app mounts never touch them).
    ctx = VizContext(
        config=config,            # type: ignore[arg-type]  # composite twin's config, if any
        service_registry=None,    # type: ignore[arg-type]
        spec_provider=lambda: spec,
        event_bus=event_bus,
        ts_store=ts_store,
        mas=mas,
    )
    app.include_router(build_spec_router(ctx), prefix=VIZ_PREFIX)
    app.include_router(build_agents_router(ctx), prefix=VIZ_PREFIX)
    if event_bus is not None:
        app.include_router(build_live_router(ctx), prefix=VIZ_PREFIX)
    if ts_store is not None:
        app.include_router(build_history_router(ctx), prefix=VIZ_PREFIX)

    if chat_agent is not None:
        from dyon.services.api.streaming import build_chat_router

        # build_chat_router only needs config for error messages; the bound agent
        # drives every response, so a missing config is harmless here.
        app.include_router(build_chat_router(config, agent=chat_agent), prefix="/api")  # type: ignore[arg-type]

    _mount_dashboard_assets(app)
    return app


def create_dashboard_app(
    config: TwinConfig,
    service_registry: ServiceRegistry,
    *,
    spec: DashboardSpec | None = None,
    event_bus: EventBus | None = None,
) -> FastAPI:
    """Build a standalone FastAPI app that serves only the visualization layer.

    Useful for running the dashboard as its own process; the in-twin path is
    ``create_app(..., include_viz=True)`` or ``mount_visualization`` on an
    existing app.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title=f"Dyon Dashboard: {config.asset_name}")
    origins = _cors_origins(config)
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins,
            allow_methods=["*"], allow_headers=["*", "x-api-key"],
        )
    mount_visualization(
        app, config, service_registry, spec=spec, event_bus=event_bus,
    )
    return app
