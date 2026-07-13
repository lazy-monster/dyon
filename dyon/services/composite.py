"""CompositeService: a service composed of other services."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class CompositeService:
    """
    A service that wraps and coordinates multiple child services.

    Starts children in dependency order, stops in reverse.
    """

    service_name: str = "composite"
    dependencies: list[str] = []

    def __init__(self, name: str, services: list):
        self.service_name = name
        self._services = services

    async def start(self) -> None:
        log.info("CompositeService '%s' starting %d services",
                 self.service_name, len(self._services))
        tasks = [asyncio.create_task(svc.start()) for svc in self._services]
        # If one child's start() raises, cancel the siblings before re-raising so
        # the composite doesn't limp along with orphaned, unwatched start tasks.
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                raise exc

    async def stop(self) -> None:
        for svc in reversed(self._services):
            try:
                await svc.stop()
            except Exception as e:
                log.error("Error stopping service in composite: %s", e)
