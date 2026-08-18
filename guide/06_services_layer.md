# 06 — The Services Layer

Every layer so far has run entirely inside the twin process. Readings go into
databases, layers talk over the event bus, and none of it is visible from outside.
The services layer opens the twin up. It gives the twin a public face that
dashboards, other software, and other twins can connect to, through two
components: a synchronised state record in Eclipse Ditto, and a FastAPI web API.

---

## Eclipse Ditto: a standard place to read twin state

Eclipse Ditto is a separate program — it runs in its own container — that keeps a
structured, always-current record for each twin, readable by any HTTP client.
Think of it as one live JSON document per twin that always reflects the latest
state. The framework keeps that document up to date for you.

The point of routing state through Ditto rather than exposing your own databases
is *standardisation*. Every Dyon twin presents the same Ditto interface,
whatever its domain. A collection twin that watches twenty component twins
(chapter 10) does not need to know each one's internal storage — it reads them all
the same way. The interface stays stable while the internals are free to change.

Every twin's Ditto record, called a **Thing**, has two features:

| Feature     | Holds                                                              |
|-------------|-------------------------------------------------------------------|
| `telemetry` | the latest value of each sensor field                             |
| `health`    | the `health_score` and the `operational_state` (running/warning/shutdown) |

You add Ditto to a twin with the client and the sync service, exactly as the pump
twin did in chapter 02:

```python
from dyon.services.ditto.client import DittoClient
from dyon.services.ditto.sync import DittoSyncService

ditto = DittoClient(self.config)

# inside build_layers():
"services": DittoSyncService(self.config, self.bus,
                             ts_store=ts, cache=cache,
                             doc_store=doc,        # needed for the events endpoint
                             ditto_client=ditto,
                             sync_interval=5),     # push every N seconds
```

When the layer initialises, it waits for the Ditto gateway to come up, then
creates a policy and the Thing (with both features). Because it creates them with
an idempotent PUT, restarting the twin simply re-establishes the same Thing.
Every `sync_interval` seconds afterwards, it writes the latest readings into the
`telemetry` feature and the current health and state into the `health` feature.

Any system can then read the twin with a single request:

```bash
curl -u ditto:ditto http://localhost:8080/api/2/things/org.example:pump_001
curl -u ditto:ditto http://localhost:8080/api/2/things/org.example:pump_001/features/telemetry/properties
curl -u ditto:ditto http://localhost:8080/api/2/things/org.example:pump_001/features/health/properties
```

This is exactly how collection twins query their members in chapter 10.

If you would rather not run Ditto, leave the sync service out of
`build_layers()` and drop the `ditto` container from your compose file. Neither
the Data layer nor the Agent layer's control tier depends on it; only the API
endpoints that read from Ditto stop working, and the rest of the API keeps
serving.

If you want the Ditto *interface* without the Ditto *server* — a composite whose
member twins share one process, a demo, a test — swap the client rather than
dropping it. `InProcessDittoClient` (`dyon.services.ditto`) implements the same
contract against an in-memory registry of Things held across the process:

```python
from dyon.services.ditto import InProcessDittoClient, shared_registry

ditto = InProcessDittoClient(self.config, shared_registry())
```

Every method the sync service and collection twins call is there with the same
signature and the same "not found" behaviour, so nothing above it can tell it
apart from the HTTP client — cross-twin reads work with no broker and no
container. Swapping back to a real deployment is a one-line change to
`DittoClient`.

---

## The FastAPI web API

The framework builds the twin's web API for you through a `create_app()` factory.
You give it the config and a `ServiceRegistry` — a small directory that lets the
API routes find the running layers they need — and it returns a FastAPI app you
serve alongside the twin:

```python
import asyncio, uvicorn
from dyon.services.api import create_app
from dyon.services.base import ServiceRegistry
from twin import PumpTwin, config

async def main():
    twin = PumpTwin(config)
    await twin.initialise()

    registry = ServiceRegistry()
    registry.register(twin.layers["services"])   # the DittoSyncService

    app = create_app(config, registry)
    server = uvicorn.Server(uvicorn.Config(
        app, host=config.api_host, port=config.api_port, log_level="warning"))
    await asyncio.gather(twin.start(), server.serve())

asyncio.run(main())
```

The registry is the link between the web layer and the twin's internals. Each
service registers under a known name, and the API routes look services up by that
name. The built-in routes expect three names, and each route only works when the
matching service is registered:

| Method | Path                       | Needs registered          | Returns                                  |
|--------|----------------------------|---------------------------|------------------------------------------|
| GET    | `/health`                  | —                         | liveness + the twin's error counters     |
| GET    | `/api/twin/state`          | `ditto_sync`              | the full Ditto Thing                     |
| GET    | `/api/twin/telemetry`      | `ditto_sync`              | the `telemetry` feature                  |
| GET    | `/api/twin/health-score`   | `ditto_sync`              | the `health` feature                     |
| GET    | `/api/twin/events?n=20`    | `ditto_sync` (with a doc store) | recent events from MongoDB         |
| POST   | `/api/twin/external`       | `data`                    | accepts a push from another twin         |
| POST   | `/api/chat`                | `intelligent`             | streams the diagnostic agent's reply     |

The `DittoSyncService` registers itself under `ditto_sync`, so the first five
routes work as soon as you register it as shown above. The last two need the data
router registered under `data` and the multi-agent system under `intelligent`;
you add those registrations when you wire up those layers.

`/health` doubles as the twin's self-report. Alongside `asset_id` and `status`
it returns a `counters` object — running totals of the things a healthy twin
should have none of, such as failed storage writes, dropped telemetry items, and
failed event-bus publishes. A monitoring system that scrapes this endpoint sees
a data-path problem as a climbing counter long before it would surface as a gap
in a chart.

### Locking the API with a key

Everything under `/api/` — including your own routes and the streaming endpoints
below — sits behind one shared secret. Set it and every request must present it:

```bash
DT_SECURITY__API_KEY=<a long random string>
```

Ordinary clients send the key as a header:

```bash
curl -H "x-api-key: $KEY" http://localhost:8500/api/twin/state
```

Two kinds of client cannot set headers: the browser's `EventSource` (used for
SSE) and WebSocket connections opened from a page. For those, the same key goes
in the URL as a query parameter — `/api/viz/stream?api_key=…` — and the
framework accepts either form. The dashboard does this for you automatically
(chapter 13); you only meet it when writing your own streaming client.

A few paths stay open by design: `/health` (so load balancers and
`dyon infra check` can probe liveness), the OpenAPI docs, and the dashboard
shell itself (the *data* behind the dashboard still needs the key — an
unauthenticated visitor gets an empty app). With no key configured, the
middleware is not installed at all and the API behaves as one open surface,
which is the right shape for local development and the wrong one for anything
exposed — production mode (chapter 03) therefore refuses to start without a key.
The key comparison is constant-time, so it cannot be guessed by timing
responses.

### Adding your own routes

Your asset will have endpoints the framework cannot anticipate. Build them on a
standard FastAPI `APIRouter` and attach it to the app:

```python
from fastapi import APIRouter

my_router = APIRouter()

@my_router.get("/api/pump/curve")
async def pump_curve():
    return {"head_m": 32.0, "flow_lpm": 120.0}

app = create_app(config, registry)
app.include_router(my_router)
```

---

## Streaming with Server-Sent Events

Most of the API follows the ordinary request–response pattern: a client asks, the
server answers, the connection closes. That suits anything that changes slowly,
where polling every few seconds is fine.

The `/api/chat` endpoint is different. It talks to the reasoning tier's
diagnostic agent (chapter 08), and an LLM produces its answer gradually, so the
endpoint streams the reply using **Server-Sent Events** (SSE) — one HTTP
connection the server pushes text into as it is generated, so the answer appears
piece by piece instead of arriving all at once. Each piece is sent as a
`data: {"chunk": "..."}` line, and a final `data: [DONE]` marks the end.

```javascript
const response = await fetch("/api/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({message: "Why is the temperature rising?", stream: true}),
});

const reader = response.body.getReader();
while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    const chunk = JSON.parse(new TextDecoder().decode(value).replace("data: ", ""));
    if (chunk.chunk) process.stdout.write(chunk.chunk);
}
```

The same SSE mechanism is well suited to live telemetry too — one persistent
connection delivering updates beats the browser opening a fresh request every
second. For the chat endpoint to do anything, the multi-agent system must be
registered under `intelligent`, which brings us to the tier that gives the twin
something worth saying. The next chapter, though, stays with the simpler kind of
response: the control tier, which acts on readings without any reasoning at all.
