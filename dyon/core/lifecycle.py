"""Lifecycle manager for orchestrating twin initialisation, running, and shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.base import AbstractDigitalTwin

log = logging.getLogger(__name__)


class TwinLifecycle:
    """
    Manages the full lifecycle of one or more digital twins.

    Usage::

        lifecycle = TwinLifecycle()
        lifecycle.add(my_twin)
        asyncio.run(lifecycle.run_forever())
    """

    def __init__(self):
        self._twins: list[AbstractDigitalTwin] = []

    def add(self, twin: AbstractDigitalTwin) -> None:
        self._twins.append(twin)

    async def initialise_all(self) -> None:
        for twin in self._twins:
            log.info("Initialising twin: %s", twin.config.asset_id)
            await twin.initialise()

    async def start_all(self) -> None:
        tasks = [asyncio.create_task(t.start()) for t in self._twins]
        await asyncio.gather(*tasks)

    async def stop_all(self) -> None:
        for twin in reversed(self._twins):
            log.info("Stopping twin: %s", twin.config.asset_id)
            await twin.stop()

    async def run_forever(self) -> None:
        """Initialise all twins, run until SIGINT/SIGTERM, then stop cleanly."""
        # Fail fast: a production-mode twin with insecure defaults must never
        # reach the run loop (checked here, before any signal handlers bind).
        from dyon.core.security import assert_production_safe

        for twin in self._twins:
            assert_production_safe(twin.config)

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _handle_signal():
            log.info("Shutdown signal received")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except NotImplementedError:
                # Windows proactor loops don't implement add_signal_handler.
                # Fall back to signal.signal and hop back onto the loop thread
                # to set the Event safely. ValueError is suppressed for signals
                # this platform can't catch (e.g. SIGTERM outside the main
                # thread), leaving the others installed.
                with suppress(ValueError):
                    signal.signal(
                        sig,
                        lambda *_: loop.call_soon_threadsafe(stop_event.set),
                    )

        await self.initialise_all()
        run_task = asyncio.create_task(self.start_all())

        await stop_event.wait()
        # Stop gracefully *before* cancelling: stop_all() flips each layer's
        # _running flag and runs its drain/cleanup against still-live loops
        # (e.g. TelemetryRouter draining its queue). Only then do we cancel and
        # await the run task, so its CancelledError is actually consumed rather
        # than left pending or silently dropped.
        await self.stop_all()
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task
        log.info("All twins stopped")
