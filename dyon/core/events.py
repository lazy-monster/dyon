"""In-process + Redis pub/sub event bus."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Import the submodule directly: events is imported during dyon.core package
# init, before the package-level `metrics` attribute is bound, so a
# ``from dyon.core import metrics`` could see a partially initialised package.
from dyon.core import metrics

log = logging.getLogger(__name__)

EventHandler = Callable[["DomainEvent"], Awaitable[None]]


@dataclass
class DomainEvent:
    event_type: str                              # e.g. "telemetry.received", "state.changed"
    source_layer: str                            # originating layer name
    source_asset: str                            # originating asset ID
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    severity: str = "info"                       # info | warning | critical

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "source_layer": self.source_layer,
            "source_asset": self.source_asset,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity,
        }


class EventBus:
    """Pub/sub bus that works both in-process and across Redis."""

    def __init__(self, redis_adapter=None):
        self._handlers: dict[str, list[EventHandler]] = {}
        self._redis = redis_adapter   # optional: cross-process fan-out
        # Strong references to in-flight handler tasks so the runtime cannot
        # garbage-collect them mid-execution. Tasks self-evict on completion.
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: DomainEvent) -> None:
        # In-process dispatch (wildcard "*" matches all)
        for handler in list(self._handlers.get(event.event_type, [])):
            self._spawn(handler, event)
        for handler in list(self._handlers.get("*", [])):
            self._spawn(handler, event)

        # Cross-process via Redis
        if self._redis:
            try:
                await self._redis.publish(
                    f"dt_events:{event.event_type}",
                    json.dumps(event.to_dict()),
                )
            except Exception as e:
                metrics.increment("eventbus.redis_publish_errors")
                log.warning("Redis event publish failed: %s", e)

    def _spawn(self, handler: EventHandler, event: DomainEvent) -> None:
        task = asyncio.create_task(self._safe_call(handler, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self, timeout: float = 5.0) -> None:
        """Wait for in-flight handler tasks; cancel stragglers after timeout."""
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        for t in pending:
            t.cancel()
        if pending:
            log.warning("EventBus close: cancelled %d slow handler(s)", len(pending))

    @staticmethod
    async def _safe_call(handler: EventHandler, event: DomainEvent) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
            # Tolerate sync handlers — they ran already; nothing to await.
        except Exception as e:
            log.error("Event handler %s raised: %s", handler, e)
