"""Service protocol and registry."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class TwinService(Protocol):
    """A named service that uses data and models to serve higher layers."""

    service_name: str
    dependencies: list[str]   # names of services this depends on

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ServiceRegistry:
    """Central registry for all services in a twin."""

    def __init__(self):
        self._services: dict[str, TwinService] = {}

    def register(self, service: TwinService) -> None:
        self._services[service.service_name] = service

    def get(self, name: str) -> TwinService:
        if name not in self._services:
            raise KeyError(f"Service '{name}' not registered")
        return self._services[name]

    def all(self) -> dict[str, TwinService]:
        return dict(self._services)

    def resolve_order(self) -> list[TwinService]:
        """Topological sort by dependencies.

        Raises ``ValueError`` if the dependency graph contains a cycle.
        """
        visited: set[str] = set()
        in_stack: set[str] = set()
        order: list[TwinService] = []

        def visit(name: str, path: list[str]) -> None:
            if name in visited:
                return
            if name in in_stack:
                cycle = " → ".join([*path, name])
                raise ValueError(f"Service dependency cycle detected: {cycle}")
            in_stack.add(name)
            svc = self._services.get(name)
            if svc is not None:
                for dep in getattr(svc, "dependencies", []):
                    visit(dep, [*path, name])
            in_stack.discard(name)
            visited.add(name)
            if svc is not None:
                order.append(svc)

        for name in self._services:
            visit(name, [])
        return order
