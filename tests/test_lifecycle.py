"""Tests for graceful twin shutdown and layer supervision (assessment §2.2).

stop() must run each layer's drain/cleanup against a still-live loop and bound
shutdown time even when a loop is parked in a long sleep; a failing layer must
cancel its siblings and surface the error rather than orphaning tasks.
"""

from __future__ import annotations

import asyncio
import contextlib

from dyon.core.base import AbstractDigitalTwin, LayerBase
from dyon.core.config import TwinConfig


class _SlowLayer(LayerBase):
    layer_name = "slow"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.drained = False

    async def start(self):
        self._running = True
        while self._running:
            await asyncio.sleep(60)        # long poll; must be cancelled promptly

    async def stop(self):
        self._running = False
        self.drained = True                # cleanup that must run on a live loop


class _FailLayer(LayerBase):
    layer_name = "fail"

    async def start(self):
        self._running = True
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")


class _Twin(AbstractDigitalTwin):
    def __init__(self, layers):
        super().__init__(TwinConfig())
        self._build = layers

    def build_layers(self):
        return {layer.layer_name: layer for layer in self._build}


async def test_graceful_shutdown_runs_drain_and_is_prompt():
    slow = _SlowLayer(TwinConfig(), None)
    twin = _Twin([slow])
    await twin.initialise()
    run = asyncio.create_task(twin.start())
    await asyncio.sleep(0.05)

    start = asyncio.get_event_loop().time()
    await twin.stop()
    with contextlib.suppress(asyncio.CancelledError):
        await run
    elapsed = asyncio.get_event_loop().time() - start

    assert slow.drained is True
    assert elapsed < 1.0          # not blocked on the 60s sleep


async def test_failing_layer_cancels_siblings_and_raises():
    slow = _SlowLayer(TwinConfig(), None)
    fail = _FailLayer(TwinConfig(), None)
    twin = _Twin([slow, fail])
    await twin.initialise()

    raised = None
    try:
        await twin.start()
    except RuntimeError as exc:
        raised = str(exc)

    assert raised == "boom"
    assert all(t.done() for t in twin._tasks)   # no orphaned tasks
