"""CollectionOrchestrator: coordinates collection-level autonomous decisions."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.collection.base import AbstractCollectionTwin

log = logging.getLogger(__name__)


class CollectionOrchestrator:
    """
    Runs multiple collection twins concurrently.

    Each collection twin has its own orchestration loop (run()), but when
    multiple collection patterns are active (e.g. both an AggregateDT and
    a NetworkDT over the same components), the orchestrator manages them.
    """

    def __init__(self, interval: int = 15):
        self._collections: list[AbstractCollectionTwin] = []
        self._interval = interval
        self._tasks: list[asyncio.Task] = []

    def add(self, collection: AbstractCollectionTwin) -> None:
        self._collections.append(collection)

    async def run_forever(self) -> None:
        self._tasks = [
            asyncio.create_task(c.run(interval=self._interval))
            for c in self._collections
        ]
        log.info(
            "CollectionOrchestrator running %d collection twins",
            len(self._tasks),
        )
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop all collection twins and cancel their tasks."""
        for c in self._collections:
            await c.stop()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
