# 05 — The Simulation and Model Layer

The data layer tells you what the asset *is* doing. This layer adds a model of
what it *should* be doing, and watches the gap between the two. That gap — the
difference between a prediction and the matching real reading — is called the
**residual**, and it is the most sensitive fault detector the framework has.

A threshold alarm only fires once a reading crosses a hard limit, which catches
sudden failures but misses slow decline. A residual catches the decline. If the
model says the temperature should be 46 °C and the sensor reads 52 °C, the
residual is +6 °C even though 52 is far below any alarm line. That tells you the
asset is running six degrees hotter than physics allows for at this operating
point — a discrepancy that often appears hours or days before any threshold is
breached.

The catch is that residuals are only as trustworthy as the model. A poorly
calibrated model produces large residuals all the time, drowning the real signal
in noise, so you validate a model against known-healthy operation before you rely
on it. With that caveat understood, let us build one.

---

## Three kinds of model

The framework supports three ways to predict an asset's behaviour, and they suit
different situations.

A **physics model** encodes the actual governing equations — fluid dynamics for a
pump, the heat equation for a furnace wall. You write it from
domain knowledge. It is interpretable, because you can read the equations and
reason about its predictions, but it takes expertise to write and its numerical
integration can be slow.

A **surrogate model** is a machine-learning model trained to approximate a physics
model. You run the physics model many times across the full range of inputs,
record the results, and fit a neural network or regressor to those input–output
pairs. The surrogate runs far faster, which makes it suitable for tight control
loops, at the cost of interpretability and of being valid only within the range
it was trained on.

A **forecaster** ignores physics entirely and projects a single field forward from
its own history, finding daily and weekly cycles and extrapolating them. It
answers "where will this value be in 24 hours if conditions hold?" rather than
"what should this value be right now?"

The first two plug into this layer's runner and feed residuals. The forecaster is
a standalone tool you call on demand, which we cover at the end.

---

## The ModelRunner

Physics and surrogate models share one interface — the `TwinModel` protocol — so
the runner can drive any mix of them the same way. You register your models with
a `ModelRunner` and it steps them on a loop:

```python
from dyon.simulation import ModelRunner

ModelRunner(self.config, self.bus,
            ts_store=ts,
            models=[physics_model, surrogate_model],  # any mix
            step_interval=1.0,                          # step every second
            residual_anomaly_threshold=10.0)            # alert when |residual| exceeds this
```

On each interval, for every model, the runner reads the latest value of each
sensor field from InfluxDB to use as inputs, calls the model's `step()`, and
writes the prediction to `asset_simulation_{model_name}`. It then pairs each
predicted field with the real reading, writes the differences to
`asset_residuals_{model_name}`, and — if any residual exceeds the threshold —
publishes a `simulation.anomaly_detected` event so the Agent layer can respond.

The pairing relies on a naming convention: a model returns each prediction under
the key `sim_<field>`, and the runner strips the `sim_` prefix to find the real
field it should compare against. Get the field names right and residuals line up
automatically; this is why a model's output names must match your
`SensorFieldSpec` names exactly.

---

## Writing a physics model

You subclass `ODEModel` and implement one method, `derivatives()`, which returns
the rate of change of each state variable. Its signature is `(self, t, y, u)`:
`t` is time in seconds, `y` is the state vector as a NumPy array, and `u` is the
single control input (a speed or power setpoint).

```python
import numpy as np
from dyon.simulation import ODEModel

class PumpODEModel(ODEModel):
    model_name = "pump_physics"   # becomes the InfluxDB measurement label

    def derivatives(self, t, y, u):
        T, P, Q = y                 # temperature, pressure, flow
        n = u / 1450.0              # speed normalised to nominal

        Q_heat = (1.0 - 0.75) * abs(P * Q) / 600.0     # heat from inefficiency
        dT = (Q_heat - (T - 25.0)) / 120.0             # heat in, decay toward 25 °C ambient
        dP = (4.2 * n**2 - P) / 5.0                    # pressure tracks speed²
        dQ = (120.0 * n - Q) / 3.0                     # flow tracks speed

        return [dT, dP, dQ]

model = PumpODEModel(
    initial_state=np.array([25.0, 4.2, 120.0]),
    state_names=["temperature_c", "pressure_bar", "flow_rate_lpm"],
    control_field="speed_rpm",   # which input field drives the model
    nominal_input=1450.0,        # value to use if that field is missing
)
```

`ODEModel` integrates your equations with SciPy's `solve_ivp` each step and
returns the new state as `sim_temperature_c`, `sim_pressure_bar`, and so on. The
`state_names` you give must match your sensor field names, so the runner can pair
each `sim_` output with its real counterpart.

---

## Writing a surrogate model

If you have a trained network exported to ONNX, wrap it with `ONNXSurrogate`:

```python
from dyon.simulation import ONNXSurrogate

surrogate = ONNXSurrogate(
    model_path="models/pump_surrogate.onnx",
    input_fields=["speed_rpm", "pressure_bar"],       # what the model reads
    output_fields=["temperature_c", "flow_rate_lpm"], # what it predicts
)
```

For a fitted scikit-learn estimator, use `SKLearnSurrogate` the same way:

```python
import joblib
from dyon.simulation import SKLearnSurrogate

surrogate = SKLearnSurrogate(
    model=joblib.load("models/pump_regressor.joblib"),
    input_fields=["speed_rpm", "pressure_bar"],
    output_fields=["temperature_c"],
)
```

Both are stateless: they read the named input fields, run inference, and return
predictions as `sim_<output_field>`. As with the physics model, the output names
must match your sensor fields for residuals to align.

---

## Writing any other model

`TwinModel` is just two attributes and two methods, so anything that can predict
from inputs can be a model — a lookup table, a rule of thumb, a remote service:

```python
from dyon.core.types import ModelType

class LookupTableModel:
    model_name = "lookup_table"
    model_type = ModelType.ML

    def __init__(self, table):
        self._table = table

    def step(self, dt, inputs):
        speed = inputs.get("speed_rpm", 1450.0)
        nearest = min(self._table, key=lambda k: abs(k - speed))
        return {"sim_temperature_c": self._table[nearest]["temp"]}

    def reset(self):
        pass
```

The `model_type` is a label from the `ModelType` enum in `dyon.core.types`
(such as `PHYSICS`, `SURROGATE`, `DISCRETE_EVENT`, or `ML`); it is descriptive
metadata and does not change how the runner treats the model.

For processes that are better described by events than by differential equations —
queues, batch runs, maintenance cycles — `SimPyModel` wraps a SimPy process as a
`TwinModel`:

```python
from dyon.simulation import SimPyModel

def machine_process(env, state):
    while True:
        state["throughput"] = state.get("throughput", 100.0)
        yield env.timeout(1)

model = SimPyModel(process_fn=machine_process,
                   output_fields=["throughput"],
                   initial_state={"throughput": 100.0})
```

Each `step(dt, inputs)` advances the SimPy clock by `dt` and returns the current
state as `sim_<field>`.

---

## Running several models at once

Because each model writes to its own measurement, you can run several together
and compare them — a physics model against a surrogate, or two surrogates trained
differently:

```python
ModelRunner(self.config, self.bus, ts_store=ts,
            models=[physics_model, onnx_surrogate, sklearn_model])
# writes asset_simulation_pump_physics, asset_simulation_onnx_surrogate, ...
# plus the matching residual measurements
```

Side by side in Grafana, their residual streams show you which model tracks the
real asset most faithfully.

---

## The forecaster

`ProphetForecaster` is not a `TwinModel` and is not driven by the runner. It is a
utility you call when you want to look ahead: it fits a Prophet model to a field's
recent history and projects it forward.

```python
from dyon.simulation import ProphetForecaster

fc = ProphetForecaster(ts_store=ts, field="temperature_c")
fc.fit(lookback_hours=48)           # train on the last two days
forecast = fc.predict(periods=24)   # project 24 hours ahead
# [{"ds": datetime, "yhat": float, "yhat_lower": float, "yhat_upper": float}, ...]
```

The reasoning and governance tiers use this when a decision depends on where a
field is heading, not just where it is now.

---

## Where the data comes from

It is worth being clear about two things the framework calls "simulation", because
they live in different packages and do different jobs.

The models in this chapter live in `dyon.simulation`. They run *inside* the
twin, alongside whatever data is arriving, and their only job is to predict and
produce residuals. They are never a source of sensor data.

The `GenericSimulator` from chapter 02 lives in `dyon.physical`. It runs as a
*separate* process and publishes synthetic readings to the twin's MQTT topic,
standing in for hardware you do not have yet. The twin cannot tell its readings
apart from a real sensor's.

The two are independent. `ModelRunner` predicts against whatever readings flow in,
whether those come from `GenericSimulator` or from real hardware. Moving from one
to the other changes nothing in the twin: you stop the simulator and let your real
sensors publish to the same MQTT topic. If your hardware does not speak MQTT, a
thin publisher bridges it — `SerialPublisher` for serial devices, or any code that
calls `router.route(readings)` directly.

```python
from dyon.physical.simulator import SerialPublisher

pub = SerialPublisher(config, port="/dev/ttyUSB0", baud=9600, parser=my_parser)
pub.connect()
```

With a model running and residuals flowing, the twin can now tell not only that a
reading is high, but that it is *higher than it should be*. The next chapter makes
all of this visible to the outside world.
