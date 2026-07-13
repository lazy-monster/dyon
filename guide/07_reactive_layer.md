# 07 — The Agent Layer: Control

The Agent layer is where the twin acts, and this chapter covers the floor it
stands on: the tier that acts without thinking. It checks readings against rules,
moves the twin through a small set of states, and can run a control loop — all
deterministically, in milliseconds, giving the same answer to the same input every
time. That predictability is the whole point. A safety alarm must fire the instant
a reading crosses a danger line, so it cannot wait on an LLM that takes seconds to
respond and may reason slightly differently each call. The reasoning and
governance tiers above handle novelty and nuance; this tier handles speed and
certainty, and they are complementary rather than competing.

The control tier offers three tools: a threshold engine with a built-in state
machine, a configurable multi-state engine for richer assets, and a PID
controller for continuous regulation.

---

## The ThresholdRuleEngine and its state machine

You met this engine in chapter 02. It evaluates your sensor thresholds (and any
custom rules) on a timer, and uses the result to drive a finite state machine.

```python
from dyon.reactive import ThresholdRuleEngine

ThresholdRuleEngine(self.config, self.bus,
                    ts_store=ts, cache=cache, doc_store=doc,
                    custom_rules=[],      # optional, see below
                    eval_interval=5)      # evaluate every N seconds
```

A finite state machine is simply a system that is always in exactly one of a fixed
set of states and moves between them only along defined transitions — like a
traffic light that is red, amber, or green but never two at once, and never skips
the sequence. The engine's machine has three states and moves between them like
this:

```
   ┌─────────┐   warning reading    ┌─────────┐
   │ RUNNING │─────────────────────▶│ WARNING │
   │         │◀──── all clear ──────│         │
   └────┬────┘                      └────┬────┘
        │ critical reading               │ critical reading
        └───────────────┬────────────────┘
                        ▼
                  ┌──────────┐
                  │ SHUTDOWN │── restart() ──▶ RUNNING
                  └──────────┘
```

On each evaluation, the engine counts how many fields sit in their critical zone
and how many sit in their warning zone, then moves the machine: any critical field
sends it to `shutdown`; otherwise a warning field promotes `running` to `warning`;
otherwise, if nothing is in warning, `warning` falls back to `running`.

The asymmetry at the bottom is deliberate and worth restating, because it is easy
to expect symmetry that is not there. The drop into `warning` clears itself once
readings recover, but the drop into `shutdown` does not. A twin that tripped on a
critical fault stays shut down until something explicitly calls `restart()` —
because a tripped asset should not silently come back to life the moment a reading
flickers into range. You decide when to restart, whether from an operator action,
the API, or the governance tier.

Every state change does two things on its own: it writes an event to MongoDB and
publishes a `state.changed` event on the bus. The current state is always in
Redis, so any layer can read it instantly:

```python
state = cache.get_state()          # "running" | "warning" | "shutdown"
state = reactive_layer.get_state() # same, from the engine object
```

And when a maintenance workflow or test needs to move the machine by hand, the
transitions are plain synchronous methods:

```python
reactive_layer.restart()         # shutdown → running
reactive_layer.recover()         # warning  → running
reactive_layer.shutdown_asset()  # force a shutdown
```

---

## Custom rules

Thresholds catch single fields crossing single limits. For anything more — a rate
of change, a combination of fields — you write a custom rule. A rule is any object
with a `rule_name` and an `evaluate(readings)` method that returns `"warning"`,
`"critical"`, or `None`:

```python
class RateOfChangeRule:
    """Flag temperature rising too fast, even while it is still in range."""
    rule_name = "temp_roc_alarm"

    def evaluate(self, readings: dict) -> str | None:
        roc = readings.get("temperature_c_roc")   # produced by the data-management loop
        if roc is None:
            return None
        if roc > 5.0:
            return "critical"
        if roc > 2.0:
            return "warning"
        return None

class CorrelationRule:
    """Flag low pressure and low flow occurring together."""
    rule_name = "low_pressure_low_flow"

    def evaluate(self, readings: dict) -> str | None:
        pressure, flow = readings.get("pressure_bar"), readings.get("flow_rate_lpm")
        if pressure is None or flow is None:
            return None
        return "critical" if (pressure < 3.0 and flow < 80.0) else None
```

The `readings` dictionary the engine passes in holds the latest value of every
sensor field, including the smoothed and rate-of-change signals the data layer
computed. Pass your rules in alongside the thresholds, and a rule returning
`"critical"` counts exactly like a critical threshold breach:

```python
ThresholdRuleEngine(self.config, self.bus, ts_store=ts, cache=cache, doc_store=doc,
                    custom_rules=[RateOfChangeRule(), CorrelationRule()])
```

For actions beyond moving the state machine, the package also offers two small,
reusable action objects you can call from your own rule or event handler:
`LogEventAction`, which records a document event, and `PublishMQTTAction`, which
sends a message to an MQTT topic.

---

## Richer assets: the MultiStateFSMRuleEngine

Three states are enough for a simple alarm, but many assets have more operating
modes than running, warning, and shutdown. `MultiStateFSMRuleEngine` lets you
define any set of states and the logic that chooses between them. You subclass it,
declare the states and their severities, and implement one method —
`compute_desired_state()` — which looks at the readings and returns the state the
twin should be in:

```python
from dyon.reactive import MultiStateFSMRuleEngine

class PumpFSM(MultiStateFSMRuleEngine):
    _states        = ["NOMINAL", "DEGRADING", "CRITICAL", "SENSOR_FAULT"]
    _initial_state = "NOMINAL"
    _severity_map  = {
        "DEGRADING":    "warning",
        "CRITICAL":     "critical",
        "SENSOR_FAULT": "warning",
    }

    def compute_desired_state(self, readings: dict) -> str | None:
        vib = readings.get("vibration_mm_s")
        if vib is None:
            return "SENSOR_FAULT"
        if vib > 5.0:
            return "CRITICAL"
        if vib > 2.5:
            return "DEGRADING"
        return "NOMINAL"
```

That single method is all you write. The base class generates a transition into
each state, so `compute_desired_state()` can move the twin to any state directly,
and it handles the rest — performing the transition, logging the change, updating
Redis, and publishing the event. Return a state name to request a move, or `None`
to stay put. The `_severity_map` decides the severity attached to each state when
it is entered; anything you leave out defaults to `warning`. The engine evaluates
every fifteen seconds by default.

### Escalating to the reasoning tier

Most state changes are routine and the control tier handles them alone — a field
crosses a line, the state moves, an alarm is recorded. Some, though, deserve
investigation rather than just an alarm. When the multi-state engine enters a
state whose severity is `warning` or `critical`, it publishes a second event
alongside `state.changed`: a `reactive.escalation_requested` event carrying a
ready-made question about what caused the change.

```python
# published automatically — you never call this yourself
DomainEvent(
    event_type="reactive.escalation_requested",
    source_layer="reactive",
    source_asset=self.config.asset_id,
    payload={
        "from_state": "NOMINAL",
        "to_state":   "DEGRADING",
        "severity":   "warning",
        "question":   "The reactive FSM transitioned from NOMINAL to DEGRADING ... "
                      "What is driving this state change and what action is recommended?",
    },
)
```

The reasoning tier (chapter 08) subscribes to this event and begins a targeted
investigation immediately, instead of waiting for its own next polling cycle. This
is the seam between the two tiers: the control tier decides *that* something
changed, in milliseconds, and hands the question of *why* to the tier built to
reason about it. You can subscribe to either event yourself:

```python
self.bus.subscribe("state.changed", on_state_change)
self.bus.subscribe("reactive.escalation_requested", on_escalation)
```

---

## Continuous control: the PIDController

Some assets need more than discrete states — they need a value held steadily at a
target. A PID controller does that. PID stands for proportional–integral–
derivative, a feedback method that answers "the value is at X, I want it at Y, how
hard should I push right now?" The proportional term reacts to the current gap,
the integral term corrects a persistent offset that lingers, and the derivative
term eases off as the gap closes quickly so the system does not overshoot.

`PIDController` reads a process variable from InfluxDB and publishes a control
command to the twin's MQTT control topic, which the asset (or its simulator)
subscribes to:

```python
from dyon.reactive import PIDController

PIDController(self.config, self.bus,
              ts_store=ts, mqtt_transport=mqtt,
              process_variable="pressure_bar",   # the field to hold steady
              setpoint=4.2,                       # the target value
              output_min=1000.0, output_max=2000.0,
              control_key="speed_rpm",            # key in the published command
              kp=2.0, ki=0.5, kd=0.1,
              sample_time=1.0)                    # compute every N seconds
```

Each cycle it publishes a message like `{"speed_rpm": 1523.4}` to
`dt/{asset_id}/control`. You can move the target at runtime:

```python
pid_layer.setpoint = 4.5
```

A PID loop and a state machine often run together: the PID handles the fast,
continuous fine-tuning (every second), while a state machine or the governance
tier above it makes the slower, strategic calls about *whether* to be regulating
at all.

---

## Versioned rules: the RuleRepository

By default, rules live in your Python code and change only when you redeploy. When
you need to add, change, or roll back rules on a running twin — and keep an audit
trail of which version was active when — `RuleRepository` stores them as versioned
rows in PostgreSQL. (It needs the `PostgresAdapter` from chapter 04, and a
Postgres instance you supply, since `infra up` does not provision one.)

```python
from dyon.reactive.rule_repository import RuleRepository, PersistedRule
from dyon.data.storage.postgres import PostgresAdapter
import os

pg = PostgresAdapter(os.environ["POSTGRES_DSN"])
await pg.connect()

repo = RuleRepository(pg, asset_id=config.asset_id)
await repo.setup()   # creates the dt_rules table if needed

await repo.upsert(PersistedRule(
    rule_name="high_temp_rule",
    condition_type="threshold",
    params={"threshold": 70.0, "direction": "high"},
    severity="critical",
))

rules   = await repo.load_active_rules()    # the current active set
history = await repo.history("high_temp_rule")  # every version, oldest first
await repo.deactivate("high_temp_rule")     # stop loading it, keep it for audit
```

Calling `upsert()` again with the same `rule_name` increments its version rather
than overwriting, so the history is preserved.

There is one wrinkle to connect this to the engine. A rule's `evaluate()` is
synchronous and cannot `await` a database read, so you do not query Postgres from
inside it. Instead, a small background task keeps an in-memory snapshot fresh, and
the rule reads the snapshot:

```python
class HotReloadRule:
    rule_name = "dynamic_high_temp"

    def __init__(self, repo: RuleRepository, target="high_temp_rule"):
        self._repo, self._target, self._snapshot = repo, target, None

    async def refresh_forever(self, interval=30):
        while True:
            active = await self._repo.load_active_rules()
            self._snapshot = next((r for r in active if r.rule_name == self._target), None)
            await asyncio.sleep(interval)

    def evaluate(self, readings: dict) -> str | None:
        rule = self._snapshot
        if rule is None:
            return None
        temp = readings.get("temperature_c")
        if temp is not None and temp > rule.params.get("threshold", 70.0):
            return rule.severity
        return None

rule = HotReloadRule(repo)
asyncio.create_task(rule.refresh_forever())
engine = ThresholdRuleEngine(config, bus, ts_store=ts, cache=cache,
                             doc_store=doc, custom_rules=[rule])
```

The engine calls `evaluate()` synchronously every cycle; the background task keeps
the parameters current. No blocking, no nested event loops.

---

The control tier gives the twin reflexes. But a reflex only knows *that*
something is wrong, never *why*. When the engine escalates a state change, it is
asking a question it cannot answer itself — and the next chapter builds the layer
that can.
