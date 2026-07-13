"""The generic live bridge: forward EventBus telemetry/state events to clients.

``GET /api/viz/stream`` (SSE) and ``WS /api/viz/ws`` subscribe to the in-process
:class:`~dyon.core.events.EventBus`, filter for ``telemetry.*`` and ``state.*``
``DomainEvent``s, and forward each as a JSON frame. This single piece of code
replaces the hand-written SSE generator every reference dashboard carried.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from dyon.core.events import DomainEvent, EventBus
    from dyon.visualization.context import VizContext

log = logging.getLogger(__name__)

# Event-type prefixes the dashboard cares about. Everything else on the bus
# (lifecycle, control, internal) is ignored by the stream.
_FORWARDED_PREFIXES = ("telemetry.", "state.")

# Per-connection buffer. Bounded so a slow client applies backpressure (drops
# oldest) instead of growing memory without limit.
_QUEUE_MAXSIZE = 256


def _wants(event: DomainEvent) -> bool:
    return event.event_type.startswith(_FORWARDED_PREFIXES)


def _frame(event: DomainEvent) -> dict:
    return {
        "event_type": event.event_type,
        "asset": event.source_asset,
        "severity": event.severity,
        "timestamp": event.timestamp.isoformat(),
        "payload": event.payload,
    }


class _Subscription:
    """A queue fed by an EventBus ``"*"`` subscription, with clean teardown."""

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        bus.subscribe("*", self._handle)

    async def _handle(self, event: DomainEvent) -> None:
        if not _wants(event):
            return
        try:
            self._queue.put_nowait(_frame(event))
        except asyncio.QueueFull:
            # Drop the oldest frame to make room — a live view favours recency.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(_frame(event))
            except asyncio.QueueEmpty:
                pass

    async def get(self) -> dict:
        return await self._queue.get()

    def close(self) -> None:
        self._bus.unsubscribe("*", self._handle)


def build_live_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.get("/stream")
    async def stream():
        if ctx.event_bus is None:
            async def _empty() -> AsyncGenerator[str, None]:
                yield 'data: {"error": "no event bus"}\n\n'
            return StreamingResponse(_empty(), media_type="text/event-stream")

        sub = _Subscription(ctx.event_bus)

        async def _generate() -> AsyncGenerator[str, None]:
            try:
                while True:
                    try:
                        frame = await asyncio.wait_for(sub.get(), timeout=15.0)
                        yield f"data: {json.dumps(frame)}\n\n"
                    except TimeoutError:
                        # Heartbeat comment keeps proxies from closing an idle SSE.
                        yield ": keep-alive\n\n"
            finally:
                sub.close()

        return StreamingResponse(_generate(), media_type="text/event-stream")

    @router.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        if ctx.event_bus is None:
            await websocket.send_json({"error": "no event bus"})
            await websocket.close()
            return
        sub = _Subscription(ctx.event_bus)
        try:
            while True:
                frame = await sub.get()
                await websocket.send_json(frame)
        except WebSocketDisconnect:
            pass
        finally:
            sub.close()

    return router
