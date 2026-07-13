# Dyon

> **Build domain-agnostic digital twins in Python — from raw sensor data to autonomous decision-making.**

[![PyPI version](https://img.shields.io/pypi/v/dyon.svg)](https://pypi.org/project/dyon/)
[![Python versions](https://img.shields.io/pypi/pyversions/dyon.svg)](https://pypi.org/project/dyon/)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-orange.svg)](LICENSE)
[![Commercial license available](https://img.shields.io/badge/commercial%20license-available-brightgreen.svg)](mailto:galisamuel97@gmail.com)

Dyon is a domain-agnostic Python framework for building **digital twins** —
live software models of a real-world asset that ingest its sensor data, store and
model it, react to it, reason about it, and act on it. The same framework works
for a pump, a wind turbine, an HVAC unit, a delivery fleet, or a manufacturing line,
because nothing in the core knows anything about your domain. You declare what
your asset's sensors look like, write a small amount of asset-specific glue, and
the framework supplies the rest.

You switch on only the capabilities your asset needs — a monitoring-only twin can
be a few lines, and the same twin can grow into a self-managing one without a
rewrite.

## Contents

- [What you can do](#what-you-can-do)
- [The dashboard](#the-dashboard)
- [Installation](#installation)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [Learn more](#learn-more)
- [License](#license)

## What you can do

With Dyon you can:

- **Ingest live telemetry** off an MQTT broker and store it — time-series,
  documents, cache, and large objects — with an audit trail, no plumbing to write.
- **Clean and score** incoming readings automatically: smoothing, validation, and
  a rolling asset-health score.
- **Model and forecast** the asset — fit a physics model, an ML surrogate, or a
  time-series forecast, and detect drift against it.
- **React without a human in the loop** — thresholds, state machines, and
  closed-loop control that turn readings into actions.
- **Diagnose faults in plain language** — LLM agents that explain *why* something
  is wrong, with a knowledge graph behind them.
- **Act autonomously** — goal-driven decisions, learned control policies, and
  escalation to a person when it matters.
- **Teach a twin by example** — learn a behaviour from demonstrations when it's
  too hard to write down as a rule or reward.
- **Serve and visualise** — a REST + streaming API and an opt-in live dashboard
  (see below).

Each of these is optional and independent: you assemble only what your asset
needs, and you never edit the framework to do it.

## The dashboard

Dyon ships an opt-in, in-browser **dashboard** — the human-facing end of a twin.
It's built from the config you already wrote: pass one flag and you get a modern
app shell whose Home tab foregrounds the asset, a Telemetry tab of KPI tiles,
trend charts, an alarm banner and event log, and an Agents tab that shows the
multi-agent system working — all updating in real time, with nothing more to
author.

```python
app = create_app(config, registry, include_viz=True)   # off by default
```

It reads the same sensor definitions you already declared, streams live updates
to the browser, and wires a chat panel to the twin's diagnostic agent — which can
answer with a chart it draws on the fly (mirrored onto a live agent canvas), or
with speech. For assets you have a 3D model of, a viewport shows the model and
recolours it as the asset's state changes; it checks the viewer's hardware first
and falls back to a 2D schematic where the compute isn't there, so the capability
is available to those who can run it and never a barrier to those who can't. And
when twins combine — a fleet, a composite, a network — one federated dashboard
views them together, drilling into each member's own full view. The client needs
no front-end build step. Chapter 13 of the guide covers it end to end;
`dyon dashboard --api <url>` serves it against a running twin.

## Installation

Dyon requires **Python 3.11+**. The backing services (MQTT broker, databases, and
the optional twin-state and knowledge-graph stores) run in Docker, so you also
need Docker with the Compose plugin.

```bash
pip install dyon
```

That installs a lean core — an MQTT→API twin with config, the REST layer, and the
client-rendered dashboard — plus the `dyon` command-line tool. Every heavy
capability is an optional extra, so you pull only what a given twin uses:

| Extra | Adds |
|---|---|
| `stores` | InfluxDB, MongoDB, Redis, MinIO, PostgreSQL adapters |
| `agents` | LangChain agents, the Neo4j knowledge graph, sentiment scoring |
| `rl` | reinforcement learning + learning-from-demonstration (SB3, gymnasium, imitation) |
| `sim` | simulation, reactive control, and surrogate ML |
| `forecast` | the Prophet forecasting chart and agent tool |
| `voice` | server-side speech (the dashboard otherwise uses the browser's own) |
| `hardware` | serial / hardware publishers |

Combine them (`pip install 'dyon[stores,agents]'`) or install everything with
`pip install 'dyon[all]'`. A feature whose extra is not installed fails with the
exact `pip install` command that provides it.

### Coming from `dt-forge`?

This framework was previously published as **`dt-forge`** (package `dt_forge`,
command `dtforge`). It is now **Dyon**, and existing projects keep working with no
changes: `import dt_forge` and `from dt_forge.… import …` transparently resolve to
`dyon`, and the `dtforge` command still runs. You will see a one-time
`DeprecationWarning` pointing you at the new name. To migrate, replace `dt_forge`
with `dyon` in your imports and `dtforge` with `dyon` on the command line. The
compatibility shim will be removed in a future major release.

## Quick start

### 1. Configure

All configuration comes from environment variables with the `DT_` prefix.
Nested fields use a **double-underscore** delimiter — `DT_MQTT__BROKER`, not
`DT_MQTT_BROKER` (the single-underscore form is silently ignored). Put them in a
`.env` file in your project directory:

```bash
DT_ASSET_ID=pump_001
DT_ASSET_TYPE=centrifugal_pump
DT_ASSET_NAME="Plant A Pump"

DT_MQTT__BROKER=localhost
DT_MQTT__PORT=1883

DT_INFLUX__URL=http://localhost:8086
DT_INFLUX__TOKEN=my-super-secret-token
DT_INFLUX__ORG=digital_twin
DT_INFLUX__BUCKET=asset_telemetry

DT_MONGO__URI=mongodb://admin:password@localhost:27017
DT_REDIS__URL=redis://localhost:6379
DT_DITTO__URL=http://localhost:8080

# Optional — only if you use the LLM diagnostic agent
DT_LLM__PROVIDER=anthropic        # openai | anthropic | ollama
DT_LLM__MODEL=claude-sonnet-4-6
DT_LLM__API_KEY=sk-ant-...
DT_NEO4J__URI=bolt://localhost:7687
```

Every field has a sensible default, so you only set what differs from the defaults.

### 2. Start the infrastructure you need

`dyon infra up` writes a `docker-compose.yml` with exactly the services your twin
needs, then runs `docker compose up -d`:

```bash
# Minimal monitoring twin (MQTT broker + the four data stores):
dyon infra up --layers data,network

# Add the twin-state service and the knowledge graph:
dyon infra up --layers data,network,services,intelligent

# Write the compose file without starting anything:
dyon infra up --layers data,network,services --generate-only
docker compose up -d
```

The command records your selection in a `.dyon-layers` file. Once the containers
are up, verify the twin can reach each one:

```bash
dyon infra check          # reads .dyon-layers automatically
```

Every reachable service prints a `✓`.

### 3. Scaffold a twin

```bash
dyon init \
  --asset-type centrifugal_pump \
  --name "Plant A Pump" \
  --asset-id pump_001
```

That writes two files into the output directory:

| File      | Purpose                                                        |
|-----------|----------------------------------------------------------------|
| `twin.py` | A runnable twin — fill in your sensor fields and run it         |
| `.env`    | Environment configuration, pre-filled with the defaults        |

The generated `twin.py` runs as soon as you fill in your sensor fields, ingesting
telemetry, storing it, scoring health, and raising warning/critical events
automatically. Add modelling, diagnosis, and autonomy as your twin grows.

### 4. Run

```bash
python twin.py            # run the module directly
# or, from the project directory:
dyon run twin          # imports twin.py and drives its lifecycle
```

If you enabled the API, the FastAPI app serves these endpoints:

| Endpoint                     | Returns                                            |
|------------------------------|----------------------------------------------------|
| `GET  /health`               | Liveness — `{asset_id, status}`                    |
| `GET  /api/twin/state`       | Current twin state                                 |
| `GET  /api/twin/telemetry`   | Latest telemetry                                   |
| `GET  /api/twin/health-score`| Current health score                               |
| `GET  /api/twin/events`      | Recent events                                      |
| `POST /api/chat`             | Streamed replies from the LLM diagnostic agent     |

Point a real device (or the bundled simulator) at the twin's telemetry topic and
it starts working immediately.

## CLI reference

```bash
dyon init        --asset-type TYPE --name NAME --asset-id ID [--out DIR]
dyon infra up    --layers ... [--out FILE] [--generate-only]
dyon infra check [--layers ...]              # default: read .dyon-layers
dyon run         [TWIN_MODULE]               # default module: "twin"
dyon train       [--algorithm SAC|TD3|PPO|A2C] [--timesteps N]
                    [--env-module M] [--save NAME]
dyon dashboard   [--api URL] [--port N] [--open/--no-open]   # serve the dashboard
```

Run any command with `--help` for the full flag list.

## Security model

Out of the box Dyon runs in **dev mode**: default credentials, open CORS, no API
key, and the API bound to `127.0.0.1`. That keeps a local twin zero-config, and is
safe on a trusted machine or lab network — but only there.

For anything exposed, switch to **production mode** with `DT_SECURITY__MODE=production`.
The twin then refuses to start until every factory-default credential is replaced,
an API key is set (`DT_SECURITY__API_KEY` — required on all `/api/*` HTTP and
WebSocket routes), and CORS is restricted to the origins you list. Enable MQTT TLS
for a remote broker, and terminate HTTPS at a reverse proxy in front of the API.
The full checklist is in [`SECURITY.md`](SECURITY.md).

## Learn more

The `guide/` folder in the source repository is the complete developer manual,
taking you from your first twin to a full worked example and a live dashboard.

## License

Dyon is dual-licensed.

- **Noncommercial use is free** under the
  [PolyForm Noncommercial License 1.0.0](LICENSE). This covers personal projects,
  academic research, teaching, evaluation, and use by nonprofit and government
  organisations. The full terms — including what counts as a permitted purpose —
  are in the [`LICENSE`](LICENSE) file.
- **Commercial use requires a separate commercial license.** Any use in or for
  the benefit of a for-profit business, or any use with an anticipated commercial
  application, needs a paid license. To arrange one, contact
  **[galisamuel97@gmail.com](mailto:galisamuel97@gmail.com)**.

PolyForm Noncommercial is a *source-available* license, not an OSI-approved
open-source license: the source is published and free to read, modify, and use
for noncommercial purposes, but commercial rights are reserved.
