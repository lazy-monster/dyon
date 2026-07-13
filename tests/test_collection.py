"""CollectionOrchestrator runs collection twins and stops them cleanly.

The orchestrator owns each twin's run loop as a task; stop() must stop every twin
and cancel/await its task so nothing is left running.
"""

from __future__ import annotations

import asyncio

from dyon.collection.orchestrator import CollectionOrchestrator


class FakeCollection:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def run(self, interval: int = 15):
        self.started = True
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise

    async def stop(self):
        self.stopped = True


async def test_orchestrator_starts_and_stops_all_twins():
    orch = CollectionOrchestrator(interval=1)
    c1, c2 = FakeCollection(), FakeCollection()
    orch.add(c1)
    orch.add(c2)

    runner = asyncio.create_task(orch.run_forever())
    await asyncio.sleep(0.05)                 # let the loops spin up
    assert c1.started and c2.started

    await orch.stop()
    assert c1.stopped and c2.stopped
    assert orch._tasks == []                  # tasks cleaned up

    runner.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await runner


async def test_stop_is_safe_with_no_twins():
    orch = CollectionOrchestrator()
    await orch.stop()                          # must not raise
    assert orch._tasks == []
