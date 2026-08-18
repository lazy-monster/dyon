"""For twins whose state is computed rather than measured.

:class:`~dyon.simulation.runner.ModelRunner` assumes a twin has two sources of
truth: sensors saying what the asset is doing, and a model saying what it should
be doing. Subtract one from the other and you get residuals, which is how a
physical twin detects that something is wrong.

Some twins have no sensors at all. A twin of a market's price level, a twin of a
process no probe can reach, a twin of anything that cannot be instrumented — for
these the model *is* the state, and there is nothing to compare it against. Left
with only the model runner, such a twin computes a trajectory internally and
publishes nothing, so its own dashboard is blank and its siblings read an empty
Thing.

:class:`ModelStateFeed` is the layer those twins want. It steps its models and
routes their state through the twin's ordinary telemetry path, so the values
land in the time-series store, the cache, and the ``telemetry.routed`` event
exactly as sensor readings would. Everything downstream — alarm thresholds, the
Ditto sync that siblings read, the live dashboard — works unchanged, because
nothing downstream can tell where a reading came from.

::

    feed = ModelStateFeed(
        config, bus, router=telemetry_router, models=[demand_model],
        control_inputs={"demand_dynamics": 2000.0},
    )

Use it *instead of* a model runner for the same models, not alongside one:
both step the model, and stepping the same integrator twice per cycle would
advance it at twice the intended rate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.writer import TelemetryRouter
    from dyon.simulation.base import TwinModel

log = logging.getLogger(__name__)


class ModelStateFeed(LayerBase):
    """Steps models and publishes their state as the twin's telemetry."""

    layer_name = "simulation"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        router: TelemetryRouter,
        models: list[TwinModel],
        step_interval: float = 1.0,
        control_inputs: dict[str, float] | None = None,
    ):
        """
        Parameters
        ----------
        router         : the twin's telemetry router; model state is routed
                         through it so it takes the same path as a reading.
        models         : the models whose state this twin publishes.
        step_interval  : seconds between steps, and the ``dt`` each step advances.
        control_inputs : per-model control value, keyed by ``model_name``. A
                         model with no entry is stepped on its nominal input.
        """
        super().__init__(config, event_bus)
        self.router = router
        self.models = {m.model_name: m for m in models}
        self.step_interval = step_interval
        self.control_inputs = dict(control_inputs or {})

    def set_control_input(self, model_name: str, value: float) -> None:
        """Set a model's control input, taking effect on the next step.

        This is the handle the rest of the system drives the model with: an
        upstream twin writes a control value here and this twin's modelled
        trajectory bends in response on the next step.
        """
        self.control_inputs[model_name] = float(value)

    def _as_fields(self, predicted: dict[str, float]) -> dict[str, float]:
        """Map a model's outputs onto the twin's sensor-field names.

        Models name their outputs ``sim_<field>`` so a runner can subtract them
        from the real reading of the same name. Here there is no real reading to
        subtract, so the prefix is dropped and the value published as the field
        itself. Outputs that do not correspond to a declared field are kept as
        they are and dropped downstream by the router's own validation.
        """
        fields: dict[str, float] = {}
        declared = set(self.config.field_names)
        for key, value in predicted.items():
            name = key.removeprefix("sim_")
            fields[name if name in declared else key] = value
        return fields

    async def step_once(self) -> dict[str, float]:
        """Step every model once and route the combined state. Returns it."""
        state: dict[str, float] = {}
        for name, model in self.models.items():
            try:
                inputs = {}
                if name in self.control_inputs:
                    control_field = getattr(model, "control_field", None)
                    if control_field:
                        inputs[control_field] = self.control_inputs[name]
                predicted = model.step(dt=self.step_interval, inputs=inputs)
                state.update(self._as_fields(predicted))
            except Exception as e:
                self.log.error("Model '%s' step error: %s", name, e)

        if state:
            await self.router.route(state)
        return state

    async def start(self) -> None:
        self._running = True
        self.log.info(
            "ModelStateFeed started for models: %s", list(self.models)
        )
        while self._running:
            await self.step_once()
            await asyncio.sleep(self.step_interval)
