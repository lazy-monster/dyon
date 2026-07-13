"""Shared runtime context handed to every visualization router.

Bundling the dependencies in one object keeps the router factories uniform and
lets ``serve.mount_visualization`` resolve stores once. Every field is optional:
an endpoint that needs a store it was not given returns a clear ``503`` rather
than failing at import or wiring time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import DocumentStore, TimeSeriesStore
    from dyon.services.base import ServiceRegistry
    from dyon.visualization.schema import DashboardSpec


@dataclass
class VizContext:
    config: TwinConfig
    service_registry: ServiceRegistry
    spec_provider: Callable[[], DashboardSpec]
    event_bus: EventBus | None = None
    ts_store: TimeSeriesStore | None = None
    doc_store: DocumentStore | None = None
    mas: object | None = None       # MultiAgentSystem, for the agents endpoint
