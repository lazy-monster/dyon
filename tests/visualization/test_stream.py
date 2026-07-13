"""The generic live bridge forwards telemetry/state events from the EventBus and
ignores everything else — the single mechanism that replaces every hand-written
SSE generator."""

from __future__ import annotations

import asyncio

from dyon.core.events import DomainEvent, EventBus
from dyon.visualization.api.live import _Subscription


async def _drain_once(sub: _Subscription):
    return await asyncio.wait_for(sub.get(), timeout=1.0)


async def test_forwards_telemetry_routed_event():
    bus = EventBus()
    sub = _Subscription(bus)
    await bus.publish(DomainEvent(
        event_type="telemetry.routed", source_layer="data",
        source_asset="pump1", payload={"temp": 42.0},
    ))
    await asyncio.sleep(0)   # let the bus dispatch the handler task
    frame = await _drain_once(sub)
    assert frame["event_type"] == "telemetry.routed"
    assert frame["payload"] == {"temp": 42.0}
    sub.close()


async def test_forwards_state_events():
    bus = EventBus()
    sub = _Subscription(bus)
    await bus.publish(DomainEvent(
        event_type="state.changed", source_layer="reactive",
        source_asset="pump1", payload={"state": "warning"},
    ))
    await asyncio.sleep(0)
    frame = await _drain_once(sub)
    assert frame["event_type"] == "state.changed"
    sub.close()


async def test_ignores_unrelated_events():
    bus = EventBus()
    sub = _Subscription(bus)
    await bus.publish(DomainEvent(
        event_type="lifecycle.started", source_layer="core",
        source_asset="pump1", payload={},
    ))
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(sub.get(), timeout=0.1)
        raised = False
    except TimeoutError:
        raised = True
    assert raised, "non-telemetry/state event should not be forwarded"
    sub.close()


async def test_unsubscribe_on_close():
    bus = EventBus()
    sub = _Subscription(bus)
    sub.close()
    assert sub._handle not in bus._handlers.get("*", [])
