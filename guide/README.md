# The Dyon Developer Guide

This is the complete manual for building a digital twin with Dyon. The thirteen
chapters are written to be read in order the first time — each builds on the
vocabulary and the layers introduced before it — and to serve as a reference
afterwards.

The arc is simple. Chapters 1–3 give you the mental model, a first running twin,
and the configuration that drives everything. Chapters 4–9 then walk up the
four-layer architecture, from raw data at the bottom to agentic decision-making at
the top: one chapter each for the Data, Simulation and Model, and Services layers,
then three for the Agent layer, whose control, reasoning, and governance tiers are
substantial enough to deserve a chapter apiece. Chapter 10 steps outside a single
twin to how twins connect and combine. Chapter 11 assembles a full twin end to
end, chapter 12 covers teaching a twin from demonstrations when you cannot write
its reward by hand, and chapter 13 puts a live dashboard on the whole thing.

| Chapter | What it covers |
|---------|----------------|
| [01 — The Mental Model](01_mental_model.md) | What the framework is, the four layers, and the four things you write |
| [02 — Your First Twin](02_your_first_twin.md) | Build and run a complete pump twin: scaffold, infrastructure, code, observe |
| [03 — Configuration and Sensors](03_config_and_sensors.md) | `TwinConfig`, `SensorFieldSpec`, derived properties, environment variables, and dev vs production mode |
| [04 — The Data Layer](04_data_layer.md) | Storing, cleaning, and scoring readings; plus text, sessions, provenance, and SQL |
| [05 — The Simulation and Model Layer](05_simulation_layer.md) | Physics models, surrogates, forecasting, and fault detection through residuals |
| [06 — The Services Layer](06_services_layer.md) | Mirroring state to Eclipse Ditto and serving the REST and streaming API, behind an API key |
| [07 — The Agent Layer: Control](07_reactive_layer.md) | The deterministic floor: threshold rules, the state machine, PID control, and escalation |
| [08 — The Agent Layer: Reasoning](08_intelligent_layer.md) | The knowledge graph and the LLM diagnostic agents that reason about faults |
| [09 — The Agent Layer: Governance](09_autonomous_layer.md) | The OODA loop, goal planning, an LLM overseer, RL policies, and human escalation |
| [10 — Connectors and Collection Twins](10_connectors_and_collections.md) | How twins reach each other, and the four ways to group many twins into one |
| [11 — A Full Worked Example](11_full_example.md) | A complete agentic pump twin, assembled from every preceding chapter |
| [12 — Learning from Demonstration](12_learning_from_demonstration.md) | Imitation and inverse RL: teaching a twin a skill it would be hard to reward |
| [13 — Visualization](13_visualization.md) | The live dashboard: KPIs, charts, alarms, a conversational panel, and an optional 3D viewport — derived from your config |

---

## The one rule

You never edit anything inside the `dyon/` package. Everything you write — your
config, your twin class, your models — lives in your own project folder beside it.
The framework provides the mechanism; you provide the domain. Keep that line clear
and the rest of the guide follows naturally.
