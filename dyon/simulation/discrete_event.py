"""SimPy discrete-event model wrapper."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from dyon.core.types import ModelType

log = logging.getLogger(__name__)


class SimPyModel:
    """
    Wraps a SimPy environment as a TwinModel.

    Users provide a generator function that defines the SimPy process.
    Each call to step() advances the simulation by dt time units.
    """

    model_name: str = "simpy_discrete_event"
    model_type: str = ModelType.DISCRETE_EVENT

    def __init__(
        self,
        process_fn: Callable[..., Any],
        output_fields: list[str],
        initial_state: dict[str, float] | None = None,
    ):
        import simpy

        self._process_fn = process_fn
        self.output_fields = output_fields
        # Keep the original initial state so reset() can restore it exactly.
        self._initial_state: dict[str, float] = dict(initial_state or {})
        self._state: dict[str, float] = dict(self._initial_state)
        self._env = simpy.Environment()
        self._proc = self._env.process(process_fn(self._env, self._state))

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        import simpy

        self._state.update(inputs)
        target = self._env.now + dt
        try:
            self._env.run(until=target)
        except simpy.core.EmptySchedule:
            pass
        except Exception as e:
            log.error("SimPy step error: %s", e)
        return {
            f"sim_{f}": round(float(self._state.get(f, 0.0)), 4)
            for f in self.output_fields
        }

    def reset(self) -> None:
        import simpy

        self._env = simpy.Environment()
        self._state = dict(self._initial_state)
        self._proc = self._env.process(self._process_fn(self._env, self._state))
