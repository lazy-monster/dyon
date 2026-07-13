"""Shutdown releases resources: Ditto client, event handlers, pooled HTTP clients.

Nothing here touches a network — fakes record whether close/await happened.
"""

from __future__ import annotations

import asyncio

from dyon.connector.api_connector import APIConnector
from dyon.core.events import DomainEvent, EventBus


async def test_eventbus_aclose_waits_for_in_flight_handler():
    bus = EventBus()
    done = {}

    async def slow(ev):
        await asyncio.sleep(0.05)
        done["ran"] = True

    bus.subscribe("*", slow)
    await bus.publish(DomainEvent(
        event_type="t", source_layer="x", source_asset="a", payload={},
    ))
    await bus.aclose()                       # must not return before slow() finishes
    assert done.get("ran") is True


async def test_eventbus_aclose_cancels_stragglers_past_timeout(caplog):
    bus = EventBus()

    async def very_slow(ev):
        await asyncio.sleep(10)

    bus.subscribe("*", very_slow)
    await bus.publish(DomainEvent(
        event_type="t", source_layer="x", source_asset="a", payload={},
    ))
    with caplog.at_level("WARNING"):
        await bus.aclose(timeout=0.05)       # give up quickly and cancel
    assert any("cancelled" in r.message for r in caplog.records)


async def test_ditto_sync_stop_closes_client():
    from dyon.services.ditto.sync import DittoSyncService

    class FakeDitto:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    svc = DittoSyncService.__new__(DittoSyncService)
    svc._stop_event = asyncio.Event()
    svc._running = True
    svc.ditto = FakeDitto()
    await svc.stop()
    assert svc.ditto.closed is True
    assert svc._stop_event.is_set()


async def test_api_connector_reuses_one_client_and_closes_it():
    conn = APIConnector({"twin_2": "http://host:8502"})
    first = conn._http()
    second = conn._http()
    assert first is second                   # one pooled client across calls
    await conn.aclose()
    assert conn._client is None
    # a fresh call after close lazily builds a new one
    assert conn._http() is not first
    await conn.aclose()
