# 03 — Configuration and Sensors

Chapter 01 called `TwinConfig` the single source of truth, and chapter 02 used it
without dwelling on it. Now we look at it properly, because almost everything the
framework does is driven by what you put here. Get the configuration right and
the layers above it largely wire themselves; get it wrong and the symptoms show
up far from the cause.

`TwinConfig` does two jobs. It describes the asset — its identity and its sensors
— and it holds the addresses and credentials of your infrastructure. The first is
unique to your twin and lives in code. The second changes between machines and
lives in environment variables. We will take them in that order.

---

## Describing the asset

```python
from dyon.core.config import TwinConfig, SensorFieldSpec

config = TwinConfig(
    asset_id="pump_001",
    asset_type="centrifugal_pump",
    asset_name="Plant A Pump",
    sensor_fields=[ ... ],
)
```

The three identity fields are short but load-bearing. `asset_id` in particular is
woven through everything: it becomes part of the MQTT topics the twin listens on,
the keys it writes to Redis, the tag it attaches to every InfluxDB point, and the
ID of its Ditto Thing. Two twins with different `asset_id`s can therefore share
the same infrastructure without their data ever mixing. `asset_type` and
`asset_name` are descriptive — they appear in metadata and logs.

`sensor_fields` is the heart of the configuration: the list of channels your
asset measures. Everything above the configuration reads this list to know what
to store, what to watch, and what to model.

---

## The anatomy of a sensor field

Each `SensorFieldSpec` describes one channel.

```python
SensorFieldSpec(
    name="temperature_c",        # the field's name, used everywhere
    unit="°C",                   # for display only
    nominal=45.0,                # the expected healthy value
    noise_std=0.3,               # how much it naturally fluctuates
    warn_threshold=60.0,         # the edge of the warning zone
    crit_threshold=75.0,         # the edge of the critical zone
    threshold_direction="high",  # which direction is dangerous
)
```

`name` is the only required field; everything else has a default. The name is the
exact string the rest of the framework uses — the key it expects in an incoming
MQTT message, the field it writes to InfluxDB, the label it shows in Ditto. Choose
it carefully and use it consistently.

`nominal` and `noise_std` describe healthy behaviour. The simulator from chapter
02 uses them to generate realistic readings (a Gaussian centred on `nominal` with
standard deviation `noise_std`). A field with `nominal` left as `None` is treated
as a derived or computed channel — the simulator skips it, since there is no
natural value to generate.

`warn_threshold`, `crit_threshold`, and `threshold_direction` define what counts
as a fault. The direction tells the framework which way is bad:

- `"high"` means the field alarms when it rises *above* the thresholds — natural
  for temperature, vibration, or pressure in a system that must not over-pressurise.
- `"low"` means it alarms when it falls *below* them — natural for a pressure,
  flow rate, or fluid level that must not drop.

So a high-direction field with `warn=60, crit=75` warns above 60 and goes
critical above 75, while a low-direction field with `warn=3.5, crit=2.5` warns
below 3.5 and goes critical below 2.5.

| Sensor          | Direction | Warn | Crit | Reads as                                  |
|-----------------|-----------|------|------|-------------------------------------------|
| `temperature_c` | high      | 60   | 75   | warn above 60, critical above 75          |
| `pressure_bar`  | low       | 3.5  | 2.5  | warn below 3.5, critical below 2.5        |
| `vibration_mm_s`| high      | 2.5  | 5.0  | warn above 2.5, critical above 5.0        |
| `flow_rate_lpm` | low       | 80   | 50   | warn below 80, critical below 50          |

Thresholds are optional. A channel you only feed into a model — a `speed_rpm` you
never alarm on — simply leaves both thresholds unset, and the control tier
ignores it:

```python
SensorFieldSpec(name="speed_rpm", nominal=1450.0, noise_std=5.0)
```

A field is only watched for faults when it has both a warn and a crit threshold.

---

## What the configuration computes for you

Once `sensor_fields` is set, `TwinConfig` exposes a handful of derived properties
so the layers never have to reassemble this information themselves. These are the
exact values the layers read at runtime:

```python
config.field_names
# ['temperature_c', 'pressure_bar', 'vibration_mm_s', 'speed_rpm']

config.thresholds
# {'temperature_c': {'warn': 60.0, 'crit': 75.0, 'low': False},
#  'pressure_bar':  {'warn': 3.5,  'crit': 2.5,  'low': True},
#  'vibration_mm_s':{'warn': 2.5,  'crit': 5.0,  'low': False}}
# speed_rpm is absent — it has no thresholds.

config.topic_telemetry   # 'dt/pump_001/telemetry'
config.topic_control     # 'dt/pump_001/control'
config.topic_state       # 'dt/pump_001/state'
config.thing_id          # 'org.example:pump_001'
```

`thresholds` is the one the control tier and the health calculator depend on:
each entry collapses the warn value, the crit value, and the direction (as the
boolean `low`) into the form those layers consume. Because they read it from
here, threshold values exist in exactly one place — the `SensorFieldSpec` you
wrote — and nowhere else.

---

## Connecting to infrastructure

The rest of `TwinConfig` describes where your backends live. These values rarely
belong in code, because they differ between your laptop, a staging box, and
production. Instead the framework reads them from environment variables, which you
keep in a `.env` file that `TwinConfig` loads automatically.

The naming rule has two parts. Every variable starts with the prefix `DT_`. For
the flat identity fields, that prefix and the field name are all you need:

```bash
DT_ASSET_ID=pump_001
DT_ASSET_TYPE=centrifugal_pump
DT_API_PORT=8501
```

The infrastructure settings are grouped into sub-sections — `mqtt`, `influx`,
`mongo`, and so on — and you reach into a section with a *double* underscore
between the section and the field:

```bash
DT_MQTT__BROKER=192.168.1.50
DT_MQTT__PORT=1883

DT_INFLUX__URL=http://influx.internal:8086
DT_INFLUX__TOKEN=my-production-token
DT_INFLUX__ORG=production

DT_LLM__PROVIDER=anthropic
DT_LLM__MODEL=claude-sonnet-4-6
DT_LLM__API_KEY=sk-ant-...
```

The double underscore is what tells the loader you mean the `broker` field
*inside* the `mqtt` section. A single underscore would be read as a flat
top-level name and ignored, so this is the one detail to be careful about.

`sensor_fields` is the only part of the configuration that cannot come from the
environment, because it is a list of structured objects rather than a string. It
always lives in your Python code.

---

## Dev mode and production mode

One sub-section deserves its own introduction, because it governs how seriously
the twin treats all the others. The `security` section has a `mode` field with
two settings, and the default is `dev`:

```bash
DT_SECURITY__MODE=dev          # the default — zero-config local development
```

In dev mode the twin starts with whatever configuration it has, including the
factory defaults for every credential above. That is deliberate: on your own
machine, against infrastructure that `dyon infra up` just created, being able to
run `python twin.py` with an empty `.env` is worth more than credential
discipline. Dev mode is also why the API binds to `127.0.0.1` unless you say
otherwise — a twin you are developing is reachable from your browser and from
nothing else.

The moment a twin is exposed beyond your machine, switch the mode:

```bash
DT_SECURITY__MODE=production
DT_SECURITY__API_KEY=<a long random string>
DT_SECURITY__CORS_ORIGINS='["https://ops.example.com"]'
```

In production mode the twin *refuses to start* until its configuration is
actually secure: every factory-default credential must be replaced, an API key
must be set, and CORS must name explicit origins. The error message lists each
offending setting, so fixing a refusal is a matter of working down the list. The
API key, once set, is required on every `/api/*` route — chapter 06 shows how
clients present it.

This is a fail-fast design. A production twin with a default MongoDB password is
not a twin with a warning in its logs; it is a twin that never comes up. The
check runs once, at startup, and costs nothing afterwards.

---

## Running several twins side by side

Because identity flows from `asset_id`, running more than one twin on the same
machine is just a matter of giving each a different one. Inline environment
variables override the `.env` file, so you can launch two pumps from the same
`twin.py`:

```bash
# terminal 1
DT_ASSET_ID=pump_001 DT_API_PORT=8501 python twin.py

# terminal 2
DT_ASSET_ID=pump_002 DT_API_PORT=8502 python twin.py
```

Or set the values directly in code, which is the pattern collection twins use in
chapter 10:

```python
config_001 = TwinConfig(asset_id="pump_001", api_port=8501, sensor_fields=[...])
config_002 = TwinConfig(asset_id="pump_002", api_port=8502, sensor_fields=[...])
```

Either way, each twin keeps its own topics, its own cache keys, and its own Ditto
Thing.

---

## The sub-config reference

You will rarely set these by hand — the defaults match the infrastructure
`dyon infra up` generates — but this is what each section controls and the
default it falls back to:

| Section  | Key fields                                | Default                                  |
|----------|-------------------------------------------|------------------------------------------|
| `mqtt`   | broker, port, keepalive, username, password | localhost:1883                         |
| `influx` | url, token, org, bucket                   | localhost:8086 / digital_twin / asset_telemetry |
| `mongo`  | uri, db                                   | localhost:27017 / digital_twin           |
| `redis`  | url, db                                   | redis://localhost:6379 / db 0            |
| `minio`  | endpoint, access_key, secret_key, bucket  | localhost:9000 / digital-twin-assets     |
| `ditto`  | url, user, password, namespace            | localhost:8080 / org.example             |
| `neo4j`  | uri, user, password                       | bolt://localhost:7687                    |
| `llm`    | provider, model, api_key, base_url, temperature, timeout_s, max_tokens, max_retries | openai / gpt-4o-mini |
| `security` | mode, api_key, cors_origins             | dev mode, no key                         |

Two sections carry fields worth knowing before you need them. `mqtt` includes
`tls`, `tls_ca_certs`, and `tls_insecure`, so a broker outside your machine can
be reached over an encrypted connection (typically on port 8883). And `llm`
bounds every call the diagnostic agent makes: `timeout_s` caps how long a single
request may take, `max_tokens` caps how long a reply may grow, and `max_retries`
covers transient provider errors. The defaults are sensible, and chapter 08
explains what they protect you from.

With the configuration understood, we can descend to the bottom of the stack. The
next chapter is the Data layer — where every reading the twin receives is stored,
cleaned, and turned into the health score the rest of the framework relies on.
