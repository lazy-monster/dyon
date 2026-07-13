# 01 — The Mental Model

This chapter has no code to run. Its job is to give you the vocabulary and the
shape of the framework, so that when you start building in the next chapter every
piece has somewhere to fit. Read it once, slowly. Everything afterwards refers
back to the ideas introduced here.

---

## What a digital twin is in Dyon

A digital twin is a long-running Python program that mirrors one real-world
asset. "Asset" is deliberately vague: it might be a centrifugal pump, a wind
turbine, an HVAC unit, or a delivery fleet. The twin takes in that asset's data
as it happens, keeps a memory of it, reasons about it, and — when you let it —
acts on it.

The framework's central idea is that *every* twin, regardless of domain, has the
same internal structure. It is built from a fixed set of **layers**, stacked from
raw data at the bottom to decision-making at the top. Each layer has a single
responsibility and knows nothing about the layers above it. You choose which
layers your asset needs and leave the rest out.

This matters because it means the framework contains no knowledge of pumps or
turbines or fleets. It provides the structure — how data flows, how layers start and
stop, how they talk to each other — and you provide the domain: what the sensors
are called, what counts as a fault, what the asset should do about it.

---

## The four layers

```
┌─────────────────────────────────────────────────────────────────────┐
│  4 · AGENT               control, reasoning, and governance         │
├─────────────────────────────────────────────────────────────────────┤
│  3 · SERVICES            expose the twin to the outside world       │
├─────────────────────────────────────────────────────────────────────┤
│  2 · SIMULATION & MODEL  predict and forecast against a model       │
├─────────────────────────────────────────────────────────────────────┤
│  1 · DATA                ingest, store, clean, and score data       │
╞═════════════════════════════════════════════════════════════════════╡
│      NETWORK             receive messages, validate their schema    │
├─────────────────────────────────────────────────────────────────────┤
│      PHYSICAL            publishers, simulators, protocol adapters  │
└─────────────────────────────────────────────────────────────────────┘
```

Read from the bottom up, each layer builds on the one below it:

1. **Data** is the foundation. It receives sensor readings (over MQTT, or any
   source you wire in), stores them in the right databases, smooths the noise out
   of them, condenses them into a single health score, and keeps a tamper-evident
   record of where each reading came from. Every layer above reads from here.

2. **Simulation and Model** runs a model of the asset alongside the real thing — a
   set of physics equations, a trained surrogate, or a forecaster. Comparing the
   model's prediction to reality tells you when the asset is drifting away from
   how it *should* behave.

3. **Services** makes the twin visible. It mirrors the twin's current state into
   Eclipse Ditto (a standard digital-twin state store) and serves a REST and
   streaming API, so that a dashboard, a person, or a peer twin can ask the twin
   what it knows.

4. **Agent** is where the twin acts, reasons, and governs itself. These are one
   layer because they form a single ascent of autonomy, from a reflex to a
   deliberated decision. It has three tiers, and each gets a chapter of its own:

   - A **control tier** (chapter 07) is the deterministic floor. It checks readings
     against the thresholds you defined, drives a small state machine (running →
     warning → shutdown), and runs closed-loop PID control. It answers in
     milliseconds and gives the same answer to the same input every time, so a
     safety response never waits on an LLM.
   - A **reasoning tier** (chapter 08) holds a knowledge graph of the asset's
     components and failure modes, and runs LLM-powered agents that work out *why*
     a symptom is occurring, not merely that it is.
   - A **governance tier** (chapter 09) runs an OODA cycle — observe, orient,
     decide, act — choosing the next action from goals, a learned
     reinforcement-learning policy, or an LLM overseer, and escalating to a human
     when the stakes call for it.

   In code these tiers are the `dyon.reactive`, `dyon.intelligent`, and
   `dyon.autonomous` modules. Keeping them separate is what lets you replace any
   one of them; keeping them under one layer is what lets the fast, certain path
   remain the safety net beneath the slow, clever one.

Beneath the Data layer sit two **enabling layers**, which you mostly configure
rather than write. The Physical layer is the publishers, simulators, and protocol
adapters that put readings on the wire. The Network layer receives those messages
and validates their schema before the Data layer trusts them. Together they are
the boundary between the physical asset and its digital counterpart. In the next
chapter you will meet both: `dyon infra up` starts the broker that is your Network
layer, and a short simulator script stands in for the Physical one until a real
asset is publishing.

Two further capabilities sit across the stack rather than inside it.
**Connectors** let one twin reach another — query it, push to it, subscribe to it
— so a twin can be useful alongside its peers rather than in isolation.
**Collection twins** group many twins into one: a fleet, a hierarchy, or a
network. Both have their own chapter.

You are never forced to use a layer. Because the layers are cumulative from the
bottom, a twin can grow through three stages without being redesigned. A
**connected** twin runs Data and Services, and monitors. A **predictive** twin
adds Simulation and Model, and gains divergence detection. An **agentic** twin
adds the Agent layer, and gains control, reasoning, and self-governance. Starting
at the first stage costs you nothing at the third. You express which stage you
are at in one place, which we will see next.

---

## The four things you write

No matter how large the twin grows, you only ever author four kinds of thing.
Everything else is supplied by the framework.

**1 — A configuration object.** `TwinConfig` names the asset, lists its sensors,
and holds the addresses of your infrastructure. Every layer is handed this object
and reads what it needs from it, so nothing about your asset is hardcoded
anywhere else.

```python
from dyon.core.config import TwinConfig, SensorFieldSpec

config = TwinConfig(
    asset_id="pump_001",
    asset_type="centrifugal_pump",
    sensor_fields=[
        SensorFieldSpec(name="temperature_c", nominal=45.0,
                        warn_threshold=60.0, crit_threshold=75.0),
        SensorFieldSpec(name="pressure_bar", nominal=4.2,
                        warn_threshold=3.5, crit_threshold=2.5,
                        threshold_direction="low"),
    ],
)
```

**2 — A twin class.** You subclass `AbstractDigitalTwin` and implement a single
method, `build_layers()`. It returns a dictionary mapping a name to each layer
instance you want active. This dictionary *is* your architecture choice — the
layers you include are the layers your twin has.

```python
from dyon.core.base import AbstractDigitalTwin

class PumpTwin(AbstractDigitalTwin):
    def build_layers(self):
        ts    = InfluxAdapter(self.config)
        doc   = MongoAdapter(self.config)
        cache = RedisAdapter(self.config)
        router = TelemetryRouter(self.config, self.bus,
                                 ts_store=ts, doc_store=doc, cache=cache)
        return {
            "data":     router,
            "reactive": ThresholdRuleEngine(self.config, self.bus,
                                            ts_store=ts, cache=cache, doc_store=doc),
        }
```

**3 — Domain models, when a layer needs them.** Some layers need asset-specific
knowledge that the framework cannot guess: the differential equations for the
Simulation and Model layer, the failure modes for the Agent layer's knowledge
graph, the reward function for its governance tier. You provide these only when
you add the layer that uses them. They are optional in exactly the same way the
layers are.

**4 — An entry point.** A few lines that build the twin and hand it to the
lifecycle manager, which runs it until you stop it.

```python
import asyncio
from dyon.core.lifecycle import TwinLifecycle

if __name__ == "__main__":
    lifecycle = TwinLifecycle()
    lifecycle.add(PumpTwin(config))
    asyncio.run(lifecycle.run_forever())
```

---

## How the layers run, and how they talk

When the lifecycle starts a twin, it calls `build_layers()` and then drives every
layer through the same three phases. First it **initialises** each layer in the
order you listed them, so a layer that prepares something (the data stores, the
Ditto thing) finishes before a later layer relies on it. Then it **starts** every
layer at once — each layer runs its own loop concurrently, because once they are
running they have no ordering between them. Finally, on shutdown, it **stops**
them in reverse order, so a lower layer is never torn down while a higher one
still depends on it.

Crucially, the layers never call each other's methods. A layer that needed to
reach into another would have to know that other exists, and the whole point of
the stack is that it does not. Instead, layers communicate through two indirect
channels.

The first is **shared storage**. The data layer writes readings to InfluxDB; the
control tier reads the latest reading back from InfluxDB. Neither holds a
reference to the other. To add, remove, or replace a layer you change the
`build_layers()` dictionary and nothing else.

The second is the **event bus**. When something worth announcing happens — a
reading arrives, the state changes, an agent decides something — a layer publishes
a `DomainEvent`. Any layer that cares subscribes to that event type and reacts.
The publisher does not know or care who is listening.

```python
# A layer announces something happened:
await self.bus.publish(DomainEvent(
    event_type="state.changed",
    source_layer="reactive",
    source_asset=self.config.asset_id,
    payload={"from": "running", "to": "warning"},
))

# A different layer listens for it:
self.bus.subscribe("state.changed", self._on_state_change)
```

A `DomainEvent` always carries its type, the layer and asset it came from, a
free-form payload, a timestamp, and a severity (`info`, `warning`, or
`critical`). Handlers run as independent tasks, and a handler that raises an
exception is logged and isolated — one failing subscriber cannot bring down the
publisher or the other subscribers.

---

## Configuration is the single source of truth

Because every layer receives the same `TwinConfig`, that object is where all
shared knowledge lives. The MQTT topic a layer subscribes to, the InfluxDB bucket
it writes to, the list of sensor fields, the thresholds that define a fault — a
layer reads each of these from `self.config`, never from a constant in its own
code. Configuration is covered in full in chapter 03; for now just hold onto the
principle, because it is what keeps the framework domain-agnostic. Change the
config and you have changed the twin.

---

## What the framework owns, and what you own

| The framework owns | You own |
|--------------------|---------|
| How layers initialise, run, and stop | Your sensor field names and thresholds |
| How events flow between layers | The equations in your simulation model |
| How telemetry is routed to storage | Your failure modes and symptoms |
| The OODA loop and goal machinery | Your goal definitions and reward function |
| The LangChain agent wiring | Which LLM you connect to it |
| The reinforcement-learning scaffolding | What "good behaviour" means for the asset |
| The collection and connector plumbing | Which twins to group, and how they relate |

The dividing line is consistent: the framework owns *mechanism*, you own
*meaning*. With that frame in place, the next chapter builds a complete, running
twin so you can see all of this in motion.
