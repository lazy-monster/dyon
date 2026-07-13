"""Physics-based ODE model using scipy.integrate.solve_ivp."""

from __future__ import annotations

import logging

import numpy as np
from scipy.integrate import solve_ivp

from dyon.core.types import ModelType

log = logging.getLogger(__name__)


class ODEModel:
    """
    Physics-based ODE model. Users subclass and override `derivatives`.

    Example::

        class PumpODE(ODEModel):
            def derivatives(self, t, y, u):
                T, P, Q = y
                ...
                return [dT, dP, dQ]

        model = PumpODE(
            initial_state=np.array([25.0, 4.2, 120.0]),
            state_names=["temperature_c", "pressure_bar", "flow_rate_lpm"],
            control_field="speed_rpm",
            nominal_input=1450.0,
        )
    """

    model_name: str = "physics_ode"
    model_type: str = ModelType.PHYSICS

    def __init__(
        self,
        initial_state: np.ndarray,
        state_names: list[str],
        control_field: str,
        nominal_input: float,
        method: str = "RK45",
    ):
        self.state = initial_state.copy()
        self.initial_state = initial_state.copy()
        self.state_names = state_names
        self.control_field = control_field
        self.nominal_input = nominal_input
        self.method = method

    def derivatives(self, t: float, y: np.ndarray, u: float) -> list[float]:
        """Override with your asset's physics equations."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement derivatives()"
        )

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        u = inputs.get(self.control_field, self.nominal_input)
        try:
            sol = solve_ivp(
                fun=lambda t, y: self.derivatives(t, y, u),
                t_span=(0, max(dt, 1e-6)),
                y0=self.state,
                method=self.method,
            )
            self.state = sol.y[:, -1]
        except Exception as e:
            log.error("ODE step failed: %s", e)
        return {
            f"sim_{name}": round(float(self.state[i]), 4)
            for i, name in enumerate(self.state_names)
        }

    def reset(self) -> None:
        self.state = self.initial_state.copy()
