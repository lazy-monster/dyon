"""Generic configurable sensor simulator."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from dyon.network.transport import MQTTTransport
from dyon.physical.base import AbstractPublisher, AbstractSimulator

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class GenericSimulator(AbstractSimulator):
    """
    Generates Gaussian-noise sensor readings around nominal values.

    Readings are published to the twin's telemetry MQTT topic.
    A fault can be injected by calling inject_fault().
    """

    def __init__(self, config: TwinConfig, publish_interval: float = 1.0):
        self._config = config
        self._transport = MQTTTransport(config, role="sim")
        self._interval = publish_interval
        self._running = False
        self._fault_active = False
        self._fault_overrides: dict[str, float] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    def inject_fault(self, field_overrides: dict[str, float]) -> None:
        """Override specific field values to simulate a fault."""
        self._fault_overrides = field_overrides
        self._fault_active = True
        log.info("Fault injected: %s", field_overrides)

    def clear_fault(self) -> None:
        self._fault_overrides = {}
        self._fault_active = False

    def step(self, dt: float = 1.0) -> dict[str, float]:
        readings: dict[str, float] = {}
        for spec in self._config.sensor_fields:
            if self._fault_active and spec.name in self._fault_overrides:
                val = self._fault_overrides[spec.name]
            elif spec.nominal is None:
                continue  # derived/computed field — not simulated here
            else:
                val = spec.nominal + random.gauss(0, spec.noise_std)
            readings[spec.name] = round(val, 4)
        if self._fault_active:
            readings["fault_injected"] = 1.0
        return readings

    def reset(self) -> None:
        self.clear_fault()

    async def run(self) -> None:
        """Connect to MQTT and publish readings at the configured interval."""
        self._running = True
        self._transport.connect()
        log.info(
            "GenericSimulator running at %.1fs interval on '%s'",
            self._interval,
            self._config.topic_telemetry,
        )
        try:
            while self._running:
                readings = self.step()
                self._transport.publish(
                    self._config.topic_telemetry, readings
                )
                await asyncio.sleep(self._interval)
        finally:
            self._transport.disconnect()
            self._running = False


class SerialPublisher(AbstractPublisher):
    """Reads from a serial port and publishes to MQTT.

    The serial port is opened lazily in ``connect()`` so the object can be
    constructed without hardware present (useful for tests and dependency
    injection).
    """

    def __init__(self, config: TwinConfig, port: str, baud: int = 9600,
                 parser=None):
        self._port = port
        self._baud = baud
        self._serial = None
        self._transport = MQTTTransport(config, role="serial")
        self._parser: Callable[[str], dict] = parser or (lambda line: {})
        self._config = config

    def connect(self) -> None:
        import serial
        if self._serial is None:
            self._serial = serial.Serial(self._port, self._baud)
        self._transport.connect()

    def disconnect(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self._transport.disconnect()

    def publish_reading(self, fields: dict[str, float]) -> None:
        self._transport.publish(self._config.topic_telemetry, fields)

    def read_once(self) -> dict[str, float] | None:
        """Read one line from the serial port, parse it, and publish it.

        Returns the parsed reading dict or ``None`` if the line could not be
        read or parsed. Call ``connect()`` first.
        """
        if self._serial is None:
            raise RuntimeError("SerialPublisher.read_once: call connect() first")
        try:
            line = self._serial.readline().decode(errors="replace").strip()
        except Exception as e:
            log.error("SerialPublisher read error: %s", e)
            return None
        if not line:
            return None
        try:
            reading = self._parser(line)
        except Exception as e:
            log.error("SerialPublisher parse error: %s", e)
            return None
        if reading:
            self.publish_reading(reading)
        return reading
