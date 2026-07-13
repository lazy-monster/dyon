"""PID controller layer wrapping simple-pid."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import TimeSeriesStore
    from dyon.network.transport import MQTTTransport

log = logging.getLogger(__name__)


class PIDController(LayerBase):
    """
    PID controller that reads a process variable from InfluxDB and publishes
    control commands to MQTT.
    """

    layer_name = "reactive_pid"
    controller_name = "pid"   # satisfies the ReactiveController protocol

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        mqtt_transport: MQTTTransport,
        process_variable: str,
        setpoint: float,
        output_min: float,
        output_max: float,
        control_key: str,
        kp: float = 1.0,
        ki: float = 0.1,
        kd: float = 0.05,
        sample_time: float = 1.0,
    ):
        super().__init__(config, event_bus)
        from simple_pid import PID

        self.ts = ts_store
        self.mqtt = mqtt_transport
        self.pv_field = process_variable
        self.control_key = control_key
        self.sample_time = sample_time

        self.pid = PID(
            Kp=kp,
            Ki=ki,
            Kd=kd,
            setpoint=setpoint,
            output_limits=(output_min, output_max),
            sample_time=sample_time,
        )

    @property
    def setpoint(self) -> float:
        return self.pid.setpoint

    @setpoint.setter
    def setpoint(self, value: float) -> None:
        self.pid.setpoint = value

    def compute(self, process_variable: float | None) -> float | None:
        if process_variable is None:
            return None
        return self.pid(process_variable)

    async def start(self) -> None:
        self._running = True
        self.log.info(
            "PIDController started (pv='%s', setpoint=%.2f)",
            self.pv_field,
            self.pid.setpoint,
        )
        while self._running:
            try:
                pv = await self.ts.aget_latest(self.pv_field)
                output = self.compute(pv)
                if output is not None:
                    self.mqtt.publish(
                        self.config.topic_control,
                        {self.control_key: round(output, 4)},
                    )
            except Exception as e:
                self.log.error("PID error: %s", e)
            await asyncio.sleep(self.sample_time)
