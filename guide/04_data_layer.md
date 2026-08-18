# 04 — The Data Layer

Everything a twin knows about its asset starts here. The data layer answers one
question — *what is the asset doing right now?* — and it answers it durably, so
the knowledge survives a restart, and quickly, so the layers above it can ask
many times a second. Those upper layers never touch sensor hardware or the
network themselves; they read what the data layer has already collected and
prepared.

The layer has two halves. The first takes readings in and stores them. The second
runs on a timer and turns those raw readings into something more useful —
smoothed values, trends, and a single health score. We will build up both, then
look at three extensions for twins whose "data" is not a stream of numbers:
free text, stateful sessions, and an auditable trail of decisions.

---

## Why four stores instead of one

A twin keeps four kinds of data, and no single database is good at all four. Each
backend is chosen for the access pattern it serves.

**InfluxDB** holds time series: the same quantity measured over and over
(temperature at 10:00:01, at 10:00:02, and so on). It is built to answer "give me
every temperature reading from the last three hours" almost instantly, which is
exactly the question the twin's models and reasoning agents ask. A general-purpose
database can store the same numbers but is far slower at this particular shape of
query.

**MongoDB** holds events: discrete things that happened, rather than continuous
measurements. A state change from `running` to `warning` is an event; a fault
injection is an event; an agent's decision is an event. These records have
irregular shapes, so a document store that accepts arbitrary JSON suits them
better than a fixed table.

**Redis** holds the latest value of everything, in memory. Reading the current
temperature from Redis takes well under a millisecond; reading it from InfluxDB
takes tens of milliseconds. Layers that need the *current* value on a tight loop —
the rule engine firing every five seconds, a dashboard polling — read from
Redis. Layers that need *history* read from InfluxDB.

**MinIO** holds binary objects: trained model files, firmware images, large
documents. You do not query these continuously; you load one at startup and keep
it in memory.

You create an adapter for each store you need at the top of `build_layers()`, then
pass them to the layers that use them:

```python
def build_layers(self):
    ts    = InfluxAdapter(self.config)   # time series
    doc   = MongoAdapter(self.config)    # events and metadata
    cache = RedisAdapter(self.config)    # latest values and current state
    obj   = MinIOAdapter(self.config)    # binary objects (only if needed)
    ...
```

---

## The TelemetryRouter: the way in

The router is the data layer's entry point. It receives a validated reading — a
plain dictionary of field names to numbers — and fans it out to the stores that
need it. "Fan out" is the key idea: one reading arrives once and is forwarded to
several destinations in a single step.

```python
router = TelemetryRouter(self.config, self.bus,
                         ts_store=ts, doc_store=doc, cache=cache)
```

For each reading, the router keeps only the fields that match your
`sensor_fields` and discards the rest. It writes those numbers to InfluxDB under
the `asset_telemetry` measurement, updates the Redis cache with each field's
latest value, and records a `last_seen` timestamp. It then publishes a
`telemetry.routed` event so any interested layer learns that fresh data has
landed. MongoDB is *not* part of this hot path — the router writes there only when
a reading is flagged as a fault injection, because normal telemetry is a time
series, not an event.

When your twin ingests over MQTT, the `MQTTIngestor` calls `router.route()` for
you, so you never invoke it directly. If your data arrives some other way — a REST
endpoint, a serial port, a database poll — your code calls `await
router.route(reading)` itself. Either way, the router is the single doorway
through which readings enter the twin.

---

## The DataManagementPipeline: from raw to useful

Raw readings are noisy and hard to reason about. The pipeline runs on a timer
(ten seconds by default) and, for each field, looks back over the last few
minutes of data to produce three derived signals:

- `{field}_smooth` — a rolling average that takes the noise out of the reading;
- `{field}_roc` — the rate of change, the difference between the two most recent
  values, which tells you the direction the field is moving;
- `{field}_quality` — a simple flag: `1.0` when the field is in range, `2.0` in
  the warning zone, `3.0` in the critical zone.

```python
DataManagementPipeline(self.config, self.bus,
                       ts_store=ts, cache=cache,
                       interval=10,        # run every N seconds
                       smooth_window=5,    # average over the last N samples
                       lookback_minutes=5) # how far back to read
```

These derived signals are written to InfluxDB under the `asset_processed`
measurement and cached in Redis.

The pipeline then computes the **health score**, which is the data layer's most
important output. It compresses every threshold check into one number from 0 to
100. Each field with thresholds carries equal weight; a field sitting in its
warning zone costs half of its weight, and one in its critical zone costs all of
it. A twin with every field in range scores 100, and one with every field
critical scores 0. The score is written to the `asset_health` measurement and
cached.

The value of this compression is that a higher layer can make a decision from a
single number. The governance tier's goal planner, in chapter 09, treats
`health_score < 50` as a risk signal without ever needing to know *which* field
dropped — the health score has already done that aggregation.

---

## How the layers above read data

Higher layers do not receive readings from the router. They read back from
InfluxDB and Redis using the adapters they were given:

```python
# Latest value of one field, from InfluxDB:
val = ts.get_latest("temperature_c")

# Latest value, from the Redis cache (faster):
val = cache.get_latest_cached("temperature_c")

# The current FSM state:
state = cache.get_state()

# Recent events, newest first:
events = doc.get_recent_events(n=20)

# Recent events of one type (used by the reasoning tier):
decisions = doc.get_events_by_type("mas_agent_AnomalyDetectionAgent", n=50)

# Several fields over a window, in one query — returns
# {field: [{"ts": unix_seconds, "value": float}, ...], ...}:
history = ts.query_recent_fields(
    fields=["temperature_c", "pressure_bar", "vibration_mm_s"], minutes=120)
```

The last call is how a dashboard pre-loads a chart: a single InfluxDB request for
many fields over a window.

---

## What ends up in InfluxDB

By the time several layers are running, InfluxDB holds a handful of measurements,
each written by a different part of the twin. Every point is tagged with
`asset_id`, so many twins can share one InfluxDB instance without their data
mixing.

| Measurement                      | Written by             | Contents                                       |
|----------------------------------|------------------------|------------------------------------------------|
| `asset_telemetry`                | TelemetryRouter        | raw sensor readings                            |
| `asset_telemetry`                | TextIngestor           | numeric signals derived from text              |
| `asset_processed`                | DataManagementPipeline | smoothed values, rate of change, quality flags |
| `asset_health`                   | DataManagementPipeline | the composite health score                     |
| `asset_simulation_{model_name}`  | ModelRunner (ch. 05)   | model predictions                              |
| `asset_residuals_{model_name}`   | ModelRunner (ch. 05)   | measured minus predicted, per field            |

---

## When the data is text: TextIngestor

Not every twin measures numbers. Some receive language — operator notes,
maintenance logs, inspection reports, support tickets. `TextIngestor` lets the rest
of the framework treat that text as if it were ordinary telemetry, by deriving a
few numeric signals from each piece of text and writing them to InfluxDB, while
keeping the original text in MongoDB for audit and search.

```python
from dyon.data.text_ingestor import TextIngestor

ingestor = TextIngestor(config, self.bus, ts_store=ts, doc_store=doc,
                        event_type="operator_note")

signals = await ingestor.ingest(
    session_id="sess_001",
    source_label="operator",
    content="The pump is making a grinding noise.",
)
# signals == {"sentiment_score": ..., "text_length": ..., "is_question": ...}
```

Each ingested message yields three signals: a `sentiment_score` from 0 (very
negative) to 1 (very positive), the `text_length` in characters, and
`is_question`, which is `1.0` when the text ends in a question mark. Sentiment is
scored with VADER by default, but you can pass your own `sentiment_fn` — a
callable from text to a float — to swap in a fine-tuned model:

```python
ingestor = TextIngestor(..., sentiment_fn=my_model.score)
```

The signals land in `asset_telemetry`, tagged with the `session_id`, so they flow
through smoothing, health scoring, and the control tier exactly like sensor
data. This is the bridge that lets a twin whose data arrives as language run on
exactly the same machinery as one whose data arrives as numbers.

---

## When interactions have a beginning and end: sessions

Some assets are not continuous — they have bounded episodes. A production run, a
maintenance job, a clinical consultation: each starts, runs through phases, and
ends. `SessionContext` holds the live state of one such episode, and
`SessionStore` persists it (in Redis, by default).

```python
from dyon.session.context import SessionContext, SessionStore

store: SessionStore[SessionContext] = SessionStore(cache, ttl=3600)
ctx = store.new_session(primary_entity_id="device_42")
ctx.add_event("note", "started maintenance")
store.save(ctx)

reloaded = store.load(ctx.session_id)
```

A `SessionContext` is deliberately domain-neutral. It carries a generated
`session_id`, a `primary_entity_id` and `secondary_entity_id` for the entities
the session is about, a `phase` string, a `health_score`, an `alert_count`, an
`event_history` you append to with `add_event()`, an `outcome` once it ends, and
an `extra` dictionary for anything else. It also tracks `started_at` and
`last_updated`, and exposes `elapsed_seconds`.

When your domain needs more than these generic fields, you do not edit
`SessionContext` — you subclass it in your own code and tell the store to use your
subclass:

```python
from dataclasses import dataclass, field
from dyon.session.context import SessionContext, SessionStore

@dataclass
class JobSessionContext(SessionContext):
    technician_id: str  = ""
    job_type:      str  = ""
    fault_codes:   list = field(default_factory=list)

def make_job_store(cache) -> SessionStore[JobSessionContext]:
    return SessionStore(cache, context_class=JobSessionContext)
```

The store is generic over the context type, so `load()` and `new_session()` return
your subclass directly, with no casting.

---

## When decisions must be auditable: ProvenanceLog

For twins that take consequential actions — offering a discount, escalating to a
human, applying a control change — you often need a record that cannot be quietly
rewritten. `ProvenanceLog` provides one: an append-only, hash-chained log stored
in a capped MongoDB collection.

```python
from dyon.data.storage.provenance import ProvenanceLog
from pymongo import MongoClient

provenance = ProvenanceLog(MongoClient(config.mongo.uri), db_name=config.mongo.db)

provenance.append(
    actor="autonomous_controller",
    inputs={"session_id": "sess_001", "discount": 0.10},
    output_summary="discount_offered",
    session_id="sess_001",
)
```

Each entry stores a SHA-256 hash of its inputs, the previous entry's hash, and its
own hash computed over the previous hash plus its body. Because every record is
chained to the one before it, altering any past record changes its hash and
breaks the link at the *next* record. If you replicate the most recent hash
somewhere outside the database, `provenance.verify_chain()` can then confirm the
entire history is intact:

```python
ok = provenance.verify_chain()   # True if every link checks out
```

You can also retrieve a session's records with `query_by_session()` or the latest
entries with `query_recent()`.

---

## When you need SQL: PostgresAdapter

InfluxDB and MongoDB cover time series and documents, but some data is genuinely
relational — versioned rule tables, compliance records, anything you want to JOIN.
`PostgresAdapter` is a thin async wrapper over a connection pool for those cases.

```python
from dyon.data.storage.postgres import PostgresAdapter

pg = PostgresAdapter("postgresql://dt_user:secret@localhost:5432/dyon")
await pg.connect()                       # once, at startup

await pg.execute("INSERT INTO rule_versions (name, body) VALUES ($1, $2)",
                 "high_temp_rule", '{"threshold": 70}')
rows  = await pg.fetch("SELECT * FROM rule_versions WHERE name = $1", "high_temp_rule")
row   = await pg.fetchrow("SELECT * FROM rule_versions WHERE id = $1", 42)
count = await pg.fetchval("SELECT COUNT(*) FROM rule_versions")

await pg.close()
```

Postgres sits slightly apart from the other backends in two ways. It is not
provisioned by `dyon infra up`, so you run the database yourself; and it has no
sub-section in `TwinConfig`, so you pass a DSN string straight to the adapter
rather than reading it from a `DT_POSTGRES__*` variable. Its main use inside the
framework is the control tier's optional `RuleRepository`, which we meet in
chapter 07.

---

## Replacing a backend

Every adapter is defined by a small protocol, so you can substitute your own. To
use TimescaleDB instead of InfluxDB, write a class with the same handful of
methods the framework calls — `write_point`, `query_recent`, `get_latest`,
`query_recent_fields`, and `close` — and pass it wherever `InfluxAdapter` would
go. Nothing else in the twin changes, because nothing else knows which time-series
store it is talking to. If you replace the document store, implement
`get_events_by_type` as well, since the reasoning tier relies on it.

## Running without a backend

Substituting a backend need not mean substituting *another* server. The framework
ships an in-process implementation of each store — `InMemoryTimeSeriesAdapter`,
`InMemoryDocumentAdapter`, `InMemoryCacheAdapter`, and `InMemoryObjectAdapter`,
all from `dyon.data` — that holds its data in bounded structures instead of a
database. They are real stores, not stubs: readings carry timestamps and are
queried by window, events stay ordered and filterable, and cache keys expire. Pass
them where the networked adapters would go and a whole twin runs with nothing
installed — the right choice for a demo, a test that wants real store semantics,
or an edge node with nowhere to put a database. What they do not do is survive the
process, so a twin that must remember across restarts still wants the networked
adapters, or `FileBackedObjectAdapter`, which persists to a local directory.

---

With the foundation in place — readings stored, cleaned, scored, and auditable —
the twin knows what its asset *is* doing. The next chapter adds a model of what it
*should* be doing, so the two can be compared.
