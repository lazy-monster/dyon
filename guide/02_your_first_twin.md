# 02 — Your First Twin

In this chapter you build a complete, running digital twin of a centrifugal pump
and watch it work. It ingests sensor readings over MQTT, stores them, mirrors its
state to Eclipse Ditto, and raises alarms when a reading crosses a threshold. In
terms of chapter 01, you will use three of the four layers: the Data layer (with
its MQTT ingest), the Services layer, and the control tier of the Agent layer. The
Simulation and Model layer, and the Agent's reasoning and governance tiers, come
in later chapters.

You will need Python 3.11+ and Docker with the Compose plugin. Install the
framework with every capability included, since this guide will eventually use
all of them:

```bash
pip install 'dyon[all]'
```

The bare `pip install dyon` is a deliberately lean core — MQTT, config, the REST
layer, and the dashboard client — and each heavy capability lives in an optional
extra (`stores` for the databases, `agents` for the LLM layers, `rl` for
learning, and so on; the README tables them all). That matters when you deploy a
twin and want only what it uses. While *learning* the framework, `[all]` saves
you meeting those extras one pip command at a time — though if you do install
narrowly, any feature whose extra is missing tells you the exact command that
provides it.

---

## Step 1 — Scaffold the project

The CLI writes a runnable starting point for you. From an empty directory:

```bash
dyon init --asset-type centrifugal_pump --name "Plant A Pump" --asset-id pump_001
```

This creates two files:

```
.env      — your asset's identity and the addresses of every backend
twin.py   — a twin class with a build_layers() method to fill in
```

The `.env` file is pre-filled with the framework's defaults, which match the
infrastructure you are about to start, so you can leave it untouched for now.

---

## Step 2 — Start the infrastructure

A twin needs backing services to talk to: an MQTT broker for incoming readings,
databases for storage, and Eclipse Ditto for canonical state. The CLI generates a
`docker-compose.yml` containing exactly the services your chosen layers need, and
starts them:

```bash
dyon infra up --layers network,data,services,intelligent
```

The `--layers` flag is the important part. Each layer pulls in the containers it
depends on, and nothing more:

| Layer flag    | Containers it adds                                            |
|---------------|---------------------------------------------------------------|
| `network`     | Mosquitto (MQTT broker)                                        |
| `data`        | InfluxDB, MongoDB, Redis, MinIO                               |
| `services`    | The Eclipse Ditto stack (MongoDB, policies, things, gateway, nginx) |
| `intelligent` | Neo4j                                                          |

The `network` layer also brings up the four data stores, because a twin that
ingests over MQTT always needs somewhere to put what it ingests. Grafana is
started in every case, on port 3000, for dashboards — the framework does not
configure it, so you add InfluxDB as a data source through its own UI.

We include `intelligent` here so Neo4j is ready when you reach chapter 08; the
pump twin in this chapter does not use it yet. Here is what each container is for
and where to reach it:

| Service       | Port(s)      | Role                                          |
|---------------|--------------|-----------------------------------------------|
| Mosquitto     | 1883, 9001   | MQTT broker (1883 plain, 9001 WebSocket)      |
| InfluxDB      | 8086         | Time-series sensor readings                   |
| MongoDB       | 27017        | Events and asset metadata                     |
| Redis         | 6379         | Latest-value cache and current state          |
| MinIO         | 9000, 9002   | Object storage (S3 API on 9000, console on 9002) |
| Eclipse Ditto | 8080         | Canonical twin state, behind an nginx proxy   |
| Neo4j         | 7474, 7687   | Knowledge graph                               |
| Grafana       | 3000         | Dashboards                                    |

The command also records your layer choice in a `.dyon-layers` file, which the
next step reads automatically.

---

## Step 3 — Wait until everything is ready

Containers start in seconds, but services like InfluxDB and Ditto take up to a
minute to finish initialising inside them. Check readiness before you run the
twin:

```bash
dyon infra check
```

This pings every service that `infra up` started and prints a pass/fail line for
each. Wait until all of them show `✓`.

---

## Step 4 — Define the twin

Open `twin.py` and replace it with the pump twin below. It has three parts: the
configuration that describes the asset, the twin class that selects the layers,
and an entry point that runs it.

```python
import asyncio
import logging
from dotenv import load_dotenv
from dyon.core.config import TwinConfig, SensorFieldSpec
from dyon.core.base import AbstractDigitalTwin
from dyon.core.lifecycle import TwinLifecycle
from dyon.data import InfluxAdapter, MongoAdapter, RedisAdapter
from dyon.data.writer import TelemetryRouter
from dyon.data.management import DataManagementPipeline
from dyon.network import MQTTIngestor
from dyon.services.ditto.client import DittoClient
from dyon.services.ditto.sync import DittoSyncService
from dyon.reactive import ThresholdRuleEngine

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

# 1. Describe the asset's sensors.
config = TwinConfig(
    sensor_fields=[
        SensorFieldSpec(name="temperature_c", nominal=45.0, noise_std=0.3,
                        warn_threshold=60.0, crit_threshold=75.0,
                        threshold_direction="high"),
        SensorFieldSpec(name="pressure_bar", nominal=4.2, noise_std=0.05,
                        warn_threshold=3.5, crit_threshold=2.5,
                        threshold_direction="low"),
        SensorFieldSpec(name="vibration_mm_s", nominal=1.5, noise_std=0.1,
                        warn_threshold=2.5, crit_threshold=5.0,
                        threshold_direction="high"),
    ]
)

# 2. Select the layers.
class PumpTwin(AbstractDigitalTwin):
    def build_layers(self):
        ts    = InfluxAdapter(self.config)   # time-series store
        doc   = MongoAdapter(self.config)    # event store
        cache = RedisAdapter(self.config)    # latest-value cache
        ditto = DittoClient(self.config)     # Eclipse Ditto client

        router = TelemetryRouter(self.config, self.bus,
                                 ts_store=ts, doc_store=doc, cache=cache)

        return {
            "network":   MQTTIngestor(self.config, self.bus, router=router),
            "data":      router,
            "data_mgmt": DataManagementPipeline(self.config, self.bus,
                                                ts_store=ts, cache=cache),
            "services":  DittoSyncService(self.config, self.bus,
                                          ts_store=ts, cache=cache,
                                          doc_store=doc, ditto_client=ditto),
            "reactive":  ThresholdRuleEngine(self.config, self.bus,
                                             ts_store=ts, cache=cache, doc_store=doc),
        }

# 3. Run it.
if __name__ == "__main__":
    lifecycle = TwinLifecycle()
    lifecycle.add(PumpTwin(config))
    asyncio.run(lifecycle.run_forever())
```

Notice how `build_layers()` mirrors chapter 01. You create the storage adapters
once at the top, then hand them to whichever layers need them. The layers share
data only through those adapters and the event bus, never by calling each other.

Each `SensorFieldSpec` carries everything the framework needs to know about one
sensor: its `nominal` healthy value, how noisy it is, and the thresholds that
define warning and critical conditions. The `threshold_direction` says which way
is bad — temperature and vibration alarm when they rise above the threshold,
pressure alarms when it falls below. Chapter 03 covers these fields in full.

---

## Step 5 — Run

```bash
python twin.py
```

`TwinLifecycle` initialises the layers in order, starts their loops concurrently,
and runs until you press `Ctrl+C`, at which point it stops every layer cleanly.

Here is what is now happening, second by second:

- On startup, the Ditto sync service creates a policy and a Thing for the pump,
  giving it `telemetry` and `health` features that external systems can read.
- The MQTT ingestor connects to Mosquitto and subscribes to
  `dt/pump_001/telemetry`. Any JSON message published there is picked up,
  stripped of non-numeric fields, and handed to the router.
- The router writes each reading to InfluxDB and updates the Redis cache, then
  announces a `telemetry.routed` event on the bus.
- Every 10 seconds, the data-management pipeline smooths the last few minutes of
  each field, computes its rate of change, flags its quality, and folds
  everything into a single 0–100 health score.
- Every 5 seconds, the Ditto sync service pushes the latest readings and health
  score into the Thing.
- Every 5 seconds, the rule engine reads the latest values and moves a state
  machine between `running`, `warning`, and `shutdown` (described below).

The twin is fully alive — but with no sensor publishing to it yet, it is watching
an empty topic. Let us give it something to watch.

---

## Step 6 — Drive it with the simulator

The framework ships a simulator that publishes synthetic readings to the same
MQTT topic your twin listens on. In a second terminal, with `twin.py` still
running, create and run this:

```python
# simulate.py
import asyncio, logging
from dotenv import load_dotenv
from dyon.physical.simulator import GenericSimulator
from twin import config

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

async def main():
    sim = GenericSimulator(config, publish_interval=1.0)
    await sim.run()

asyncio.run(main())
```

```bash
python simulate.py
```

The simulator reads the same `config`, so it knows each field's `nominal` value
and noise. Every second it publishes a reading drawn from a Gaussian around
nominal. Watch the twin's terminal: it routes each reading, and the health score
settles near 100 because every value is in range.

### Driving the state machine

The rule engine's state machine is the heart of this chapter, so it is worth
seeing how it moves. On every evaluation it counts how many fields are in their
warning zone and how many are in their critical zone, then:

- if any field is critical, it goes to `shutdown`;
- otherwise, if any field is in warning and it was `running`, it goes to
  `warning`;
- otherwise, if nothing is in warning and it was `warning`, it returns to
  `running`.

You can trigger each path by injecting a fault into the simulator. From a Python
shell that imports your running `sim` object — or by editing `simulate.py` to call
these — try a **warning-level** fault first:

```python
sim.inject_fault({"temperature_c": 65.0})   # above warn (60), below crit (75)
# within ~5s the twin moves running → warning
sim.clear_fault()
# within ~5s it returns warning → running
```

A warning is self-clearing: once the value comes back into range, the engine
moves the twin back to `running` on its own. A **critical** fault behaves
differently, and the difference is deliberate:

```python
sim.inject_fault({"temperature_c": 80.0})    # above crit (75)
# within ~5s the twin moves running → shutdown
sim.clear_fault()
# the twin stays in shutdown — it does not come back by itself
```

`shutdown` is a latching state. A pump that tripped on a critical fault should not
silently restart the moment the reading looks better; bringing it back is a
deliberate act. The engine exposes a `restart` transition for exactly this, and
you decide when to fire it — from an operator action, a maintenance workflow, or
the governance tier in chapter 09. Until then the twin holds at `shutdown`,
which is the safe default.

Every state change is recorded as an event in MongoDB and published on the bus as
a `state.changed` event, so any higher layer can react to it.

---

## Step 7 — See where the data went

Everything the twin does leaves a trace you can inspect directly.

**InfluxDB** (http://localhost:8086, log in as `admin` / `password`) holds the
time-series data in the `asset_telemetry` bucket, across three measurements:

| Measurement       | Written by               | Contents                                  |
|-------------------|--------------------------|-------------------------------------------|
| `asset_telemetry` | the router               | raw readings as they arrive               |
| `asset_processed` | the data-management loop | smoothed values, rate of change, quality  |
| `asset_health`    | the data-management loop | the composite 0–100 health score          |

Filter by the tag `asset_id = pump_001` to scope any query to this twin.

**Redis** (`localhost:6379`, no password) holds the fast-access state:

```bash
redis-cli
> GET dt:pump_001:state                  # current FSM state
> GET dt:pump_001:latest:temperature_c   # latest cached value of a field
> KEYS dt:pump_001:*                      # everything cached for this twin
```

**MongoDB** (`localhost:27017`, `admin` / `password`, database `digital_twin`)
holds the document records. The `assets` collection has one metadata record per
twin; the `events` collection has the discrete events — fault injections and
state changes — newest entries last.

**Eclipse Ditto** (http://localhost:8080, basic auth `ditto` / `ditto`) holds the
canonical state, which is what an external dashboard or integration would read:

```bash
curl -u ditto:ditto http://localhost:8080/api/2/things/org.example:pump_001
curl -u ditto:ditto http://localhost:8080/api/2/things/org.example:pump_001/features/telemetry/properties
curl -u ditto:ditto http://localhost:8080/api/2/things/org.example:pump_001/features/health/properties
```

The `health` feature carries both the `health_score` and the current
`operational_state`, so a consumer can read the twin's condition in one request.

---

You now have a working twin: it ingests, stores, processes, mirrors, and reacts.
The remaining chapters add capability one layer at a time, but the shape never
changes — you describe the asset in `TwinConfig`, you select layers in
`build_layers()`, and you run the lifecycle. Next, chapter 03 looks closely at
that configuration object, because getting it right is what makes everything
above it work.
