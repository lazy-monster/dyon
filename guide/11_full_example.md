# 11 — A Full Worked Example

This chapter assembles everything the guide has covered into one complete,
runnable twin: a centrifugal pump with all four layers, a physics model, a
knowledge graph, and a diagnostic agent. Nothing here is new — it is the pieces
from chapters 02 through 09 wired together in a single `build_layers()`. Read it as
a template. To turn it into a twin of *your* asset you change only three things:
the sensor fields, the physics equations, and the knowledge-graph contents. The
framework code never changes.

The project is three small files plus configuration:

```
my_pump_twin/
├── .env          — identity and backend addresses
├── twin.py       — the twin: sensors, model, graph, and build_layers()
├── run.py        — the entry point
└── simulate.py   — synthetic readings for testing
```

---

## twin.py

The file has four parts. First the sensors, which define everything the layers
above will store and watch (chapter 03). Note `flow_rate_lpm` and `speed_rpm` carry
no thresholds — they feed the model but are not alarmed on.

```python
import asyncio
import numpy as np
from dotenv import load_dotenv
from neo4j import GraphDatabase

from dyon.core.config import TwinConfig, SensorFieldSpec
from dyon.core.base import AbstractDigitalTwin
from dyon.data import InfluxAdapter, MongoAdapter, RedisAdapter
from dyon.data.writer import TelemetryRouter
from dyon.data.management import DataManagementPipeline
from dyon.network import MQTTIngestor
from dyon.simulation import ODEModel, ModelRunner
from dyon.services.ditto.client import DittoClient
from dyon.services.ditto.sync import DittoSyncService
from dyon.reactive import ThresholdRuleEngine
from dyon.intelligent import (
    KnowledgeGraph, KnowledgeGraphSpec, FailureMode, SymptomMapping,
    DiagnosticAgent, MultiAgentSystem,
)
from dyon.intelligent.agent import build_llm
from dyon.autonomous import OODALoop, GoalPlanner, Goal

load_dotenv()

# 1 — Sensors
config = TwinConfig(sensor_fields=[
    SensorFieldSpec(name="temperature_c", nominal=45.0, noise_std=0.3,
                    warn_threshold=60.0, crit_threshold=75.0, threshold_direction="high"),
    SensorFieldSpec(name="pressure_bar", nominal=4.2, noise_std=0.05,
                    warn_threshold=3.5, crit_threshold=2.5, threshold_direction="low"),
    SensorFieldSpec(name="flow_rate_lpm", nominal=120.0, noise_std=2.0),
    SensorFieldSpec(name="vibration_mm_s", nominal=1.5, noise_std=0.1,
                    warn_threshold=2.5, crit_threshold=5.0, threshold_direction="high"),
    SensorFieldSpec(name="speed_rpm", nominal=1450.0, noise_std=5.0),
])
```

Second the physics model, whose predictions the simulation and model layer compares against
reality to produce residuals (chapter 05):

```python
# 2 — Physics model
class PumpODE(ODEModel):
    model_name = "pump_physics"

    def derivatives(self, t, y, u):
        T, P, Q = y
        n = u / 1450.0
        Q_heat = (1.0 - 0.75) * abs(P * Q) / 600.0
        dT = (Q_heat - (T - 25.0)) / 120.0
        dP = (4.2 * n**2 - P) / 5.0
        dQ = (120.0 * n - Q) / 3.0
        return [dT, dP, dQ]

physics_model = PumpODE(
    initial_state=np.array([25.0, 4.2, 120.0]),
    state_names=["temperature_c", "pressure_bar", "flow_rate_lpm"],
    control_field="speed_rpm", nominal_input=1450.0,
)
```

Third the knowledge graph, which gives the diagnostic agent the failure knowledge
to reason with (chapter 08):

```python
# 3 — Knowledge graph
kg_spec = KnowledgeGraphSpec(
    components=["bearing", "seal", "impeller", "motor", "shaft"],
    failure_modes=[
        FailureMode(name="overheating", severity="high",
                    maintenance_actions=["check_cooling_system", "inspect_bearing"],
                    affected_components=["bearing", "motor"]),
        FailureMode(name="cavitation", severity="high",
                    maintenance_actions=["check_inlet_pressure", "reduce_speed"],
                    affected_components=["impeller"]),
        FailureMode(name="bearing_wear", severity="medium",
                    maintenance_actions=["lubricate_bearing", "replace_bearing"],
                    affected_components=["bearing"]),
    ],
    symptom_mappings=[
        SymptomMapping(symptom_name="high_temperature", sensor_field="temperature_c",
                       threshold=60.0, direction="high",
                       failure_modes=["overheating", "bearing_wear"]),
        SymptomMapping(symptom_name="low_pressure", sensor_field="pressure_bar",
                       threshold=3.5, direction="low", failure_modes=["cavitation"]),
        SymptomMapping(symptom_name="high_vibration", sensor_field="vibration_mm_s",
                       threshold=2.5, direction="high",
                       failure_modes=["bearing_wear", "cavitation"]),
    ],
)
```

Fourth the twin itself. This is where everything comes together. The pattern is the
one chapter 09 introduced: build the storage adapters once, build the control and
reasoning tiers and keep references, then hand those references to the OODA loop
so it can observe and act on them.

```python
# 4 — The twin
class PumpTwin(AbstractDigitalTwin):
    def build_layers(self):
        ts    = InfluxAdapter(self.config)
        doc   = MongoAdapter(self.config)
        cache = RedisAdapter(self.config)
        ditto = DittoClient(self.config)

        driver = GraphDatabase.driver(self.config.neo4j.uri,
                                      auth=(self.config.neo4j.user, self.config.neo4j.password))
        kg = KnowledgeGraph(self.config, driver)
        kg.setup_from_spec(kg_spec)

        router   = TelemetryRouter(self.config, self.bus, ts_store=ts, doc_store=doc, cache=cache)
        reactive = ThresholdRuleEngine(self.config, self.bus, ts_store=ts, cache=cache, doc_store=doc)

        agent = DiagnosticAgent(self.config, llm=build_llm(self.config),
                                ditto_client=ditto, ts_store=ts, doc_store=doc, knowledge_graph=kg)
        mas = MultiAgentSystem(self.config, self.bus, agents=[agent],
                               monitor_interval=60, cache=cache, doc_store=doc)

        planner = GoalPlanner(goals=[
            Goal("uptime", "Avoid entering shutdown state", priority=20),
            Goal("health", "Keep health score above 80", priority=10),
        ])
        ooda = OODALoop(self.config, self.bus,
                        ts_store=ts, cache=cache, doc_store=doc, ditto_client=ditto,
                        models={"pump_physics": physics_model},
                        reactive=reactive, mas=mas, connectors=[], planner=planner)

        return {
            "network":     MQTTIngestor(self.config, self.bus, router=router),
            "data":        router,
            "data_mgmt":   DataManagementPipeline(self.config, self.bus, ts_store=ts, cache=cache),
            "simulation":  ModelRunner(self.config, self.bus, ts_store=ts, models=[physics_model]),
            "services":    DittoSyncService(self.config, self.bus, ts_store=ts, cache=cache,
                                            doc_store=doc, ditto_client=ditto),
            "reactive":    reactive,
            "intelligent": mas,
            "autonomous":  ooda,
        }
```

The dictionary keys set the order in which layers initialise — `network` and
`services` first so the MQTT subscription and the Ditto Thing exist before the rest
relies on them, and `autonomous` last. Once running, every layer loops
concurrently.

---

## run.py

```python
import asyncio
from dotenv import load_dotenv
from dyon.core.lifecycle import TwinLifecycle
from twin import PumpTwin, config

load_dotenv()
lifecycle = TwinLifecycle()
lifecycle.add(PumpTwin(config))
asyncio.run(lifecycle.run_forever())
```

---

## simulate.py

With no hardware attached, the simulator from chapter 02 feeds the twin. Here it
runs normally for thirty seconds, then injects a critical bearing fault — high
temperature and high vibration together:

```python
import asyncio
from dotenv import load_dotenv
from dyon.physical.simulator import GenericSimulator
from twin import config

load_dotenv()

async def main():
    sim = GenericSimulator(config, publish_interval=1.0)

    async def inject_later():
        await asyncio.sleep(30)
        print("Injecting bearing fault...")
        sim.inject_fault({"temperature_c": 78.0, "vibration_mm_s": 6.0})  # both above critical

    await asyncio.gather(sim.run(), inject_later())

asyncio.run(main())
```

---

## .env

```bash
DT_ASSET_ID=pump_001
DT_ASSET_TYPE=centrifugal_pump
DT_ASSET_NAME=Plant A Pump

DT_MQTT__BROKER=localhost
DT_INFLUX__URL=http://localhost:8086
DT_INFLUX__TOKEN=my-super-secret-token
DT_INFLUX__ORG=digital_twin
DT_INFLUX__BUCKET=asset_telemetry
DT_MONGO__URI=mongodb://admin:password@localhost:27017
DT_REDIS__URL=redis://localhost:6379
DT_DITTO__URL=http://localhost:8080
DT_NEO4J__URI=bolt://localhost:7687
DT_NEO4J__PASSWORD=password

DT_LLM__PROVIDER=anthropic
DT_LLM__MODEL=claude-sonnet-4-6
DT_LLM__API_KEY=sk-ant-...
```

The credentials here are the dev-mode defaults that match what `dyon infra up`
provisions — right for this local walkthrough, and exactly what production mode
(chapter 03) would refuse to start with. A deployed version of this twin swaps
each of them for a real secret, sets `DT_SECURITY__MODE=production`, and gains
an API key on every route (chapter 06) as part of the same switch.

---

## Running it, and what you will see

Start the infrastructure, run the twin in one terminal and the simulator in
another:

```bash
dyon infra up --layers network,data,services,intelligent
dyon infra check
python run.py        # terminal 1
python simulate.py   # terminal 2
```

On startup the layers announce themselves, then the fault propagates up the stack:

```
INFO  dyon.twin.pump_001          — Initialising layer: network
INFO  dyon.service_ditto.pump_001 — Ditto gateway ready (HTTP 200)
INFO  dyon.service_ditto.pump_001 — Ditto Thing 'org.example:pump_001' ready
INFO  dyon.intelligent.pump_001   — Knowledge graph built: 5 components, 3 failure modes
INFO  dyon.network.pump_001       — MQTT ingestor listening on 'dt/pump_001/telemetry'
INFO  dyon.simulation.pump_001    — ModelRunner started with models: ['pump_physics']
INFO  dyon.reactive.pump_001      — ThresholdRuleEngine started (interval=5s)
INFO  dyon.intelligent.pump_001   — MAS started with 1 agents (max_concurrent=1)
INFO  dyon.autonomous.pump_001    — OODA autonomous loop started (interval=5s)

# ... 30 seconds in, the fault arrives ...
WARNING dyon.reactive.pump_001    — State: running → shutdown
WARNING dyon.autonomous.pump_001  — Human intervention requested: Asset is in shutdown state
```

Trace that last sequence against the chapters. The router stored the faulty reading
(chapter 04); the threshold engine saw two fields go critical and drove the state
machine to `shutdown` (chapter 07); the OODA loop's planner read that shutdown state
as critical risk requiring a human, and the act step logged the request and would
notify anyone you wired in (chapter 09). The twin reasoned its way from a raw
reading to an escalation, across four layers, with no domain code beyond the three
declarations at the top of `twin.py`.

One thing to expect: `shutdown` latches. Even if you clear the fault, the twin stays
shut down until something calls `reactive.restart()`, exactly as chapter 07
described — a tripped pump should not restart itself.

---

## Adapting it to your asset

| To change…                          | Edit…                                          |
|-------------------------------------|------------------------------------------------|
| sensor names, nominals, thresholds  | `sensor_fields` in `TwinConfig`                |
| the physics                         | `derivatives()` in your `ODEModel` subclass    |
| the state vector                    | `initial_state` and `state_names`              |
| components, failure modes, symptoms | the `KnowledgeGraphSpec`                        |
| what counts as a threat             | the `GoalPlanner` goals (or a subclass)         |
| identity and backends               | `.env`                                          |

That is the whole framework in one asset. The next chapter steps beyond it, to a
twin whose right behaviour cannot be written as a rule or a reward — and must be
learned from example.
