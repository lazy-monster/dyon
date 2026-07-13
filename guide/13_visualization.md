# 13 — Visualization

Everything the twin knows by now lives behind an API. Readings are in InfluxDB,
state is in Redis, the diagnostic agent answers on `/api/chat`, and the services
layer (chapter 06) already noted that its Server-Sent Events mechanism "is well
suited to live telemetry too." This chapter follows that thread to its
conclusion: a live, in-browser dashboard that puts a human face on the twin — a
3D (or schematic) view of the asset that recolours itself as the asset's state
changes, trend charts and KPI tiles that update in real time, a panel that shows
the multi-agent system working, and a conversational interface you can type or
speak to that answers from live data and draws charts on request.

The dashboard is a first-class part of the framework, but it is **entirely
opt-in**. A twin that never asks for it behaves exactly as it did in every
previous chapter. You turn it on with a single flag or a single function call,
and from that point on the framework reads your existing `TwinConfig` and builds
a working dashboard with no further authoring.

---

## The one-line dashboard

You already build the twin's web API with `create_app()` (chapter 06). To get a
dashboard, pass `include_viz=True`:

```python
from dyon.services.api import create_app

app = create_app(config, registry, include_viz=True)
```

That single flag mounts two things onto the same app: a set of read-only routes
under `/api/viz/*` that serve the dashboard's data, and the static dashboard
client itself at `/dashboard`. Open `http://localhost:8000/dashboard/` and you
are looking at your twin. Leave the flag off — its default — and not one byte of
the app changes.

If you would rather keep the dashboard in its own process, or point a dashboard
at a twin that is already running, the CLI serves the bundled client for you:

```bash
dyon dashboard --api http://localhost:8000
```

This serves the dashboard's static files locally and points them at the twin's
API, then opens your browser. The twin needs no extra wiring: in dev mode,
mounting the dashboard opens CORS on the twin's API so a client served from
anywhere can read it; in production mode (chapter 03) the twin answers only the
origins named in `DT_SECURITY__CORS_ORIGINS`, so list the dashboard's origin
there. Use `--port` to change the local port and `--no-open` to skip launching
the browser.

When the twin has an API key set (chapter 06), the dashboard authenticates
itself without help: open it as `/dashboard/?api_key=<key>` once and the client
remembers the key in the browser's local storage, sending it as a header on
ordinary requests and as a query parameter on its live streams — the two forms
the twin accepts. Without the key, the dashboard shell loads but every panel
stays empty.

For full control you can also mount the dashboard onto any FastAPI app you build
yourself:

```python
from dyon.visualization import mount_visualization

mount_visualization(app, config, registry)
```

`mount_visualization` discovers the twin's time-series store, document store,
multi-agent system, and event bus from the service registry, so a fully wired
twin needs no further arguments. Everything `create_app(..., include_viz=True)`
does, it does by calling this function.

---

## What you get for free

The dashboard is built from a **dashboard specification** — a description of
which panels to show and what each one is bound to. You do not have to write that
specification. The framework derives a complete one from your `TwinConfig`,
reading the same `sensor_fields` you declared back in chapter 03. From a config
with a `temperature_c` field (warn at 60, critical at 75) and a `pressure_bar`
field (warn at 3, critical at 2, low direction), the derived dashboard gives you:

- **A KPI tile per sensor field**, showing the latest value, its unit, and a
  small sparkline of recent history. A tile turns amber or red when its reading
  crosses the warning or critical threshold you set on the field.
- **Trend charts**, one per unit, plotting every field that shares that unit on a
  common axis so the scales line up.
- **An alarm banner** driven by your fields' thresholds — the same warn/critical
  levels the control tier already uses, surfaced visually.
- **A scrolling event log** of the twin's recent events.
- **A chat panel** wired to the diagnostic agent, so an operator can ask the twin
  questions without leaving the page.
- **An agents panel** that shows the multi-agent system in action — one card per
  agent, updating as each agent observes, reasons, and acts.

Crucially, the thresholds and directions on the dashboard are *your* thresholds.
The framework reads `warn_threshold`, `crit_threshold`, and `threshold_direction`
straight off each `SensorFieldSpec`; a field you marked as alarming when it falls
too *low* (like pressure) colours and alarms in the right direction without you
saying so twice.

---

## How the dashboard is laid out

The client is not a flat wall of widgets. It is an **app shell**: a sidebar on
the left for navigating between views, a stage in the middle that holds the
active view, and a chat dock that slides in from the right (and a floating chat
button to summon it). The shell carries a light/dark theme toggle, persisted in
the browser; a spec can also ship its own `theme` tokens to brand the dashboard.

A single twin's view inside that stage is **tabbed**, so the most important thing
is always the first thing you see:

- **Home** foregrounds the asset itself — the 3D (or schematic) view — alongside
  a live **agent-chart canvas** and an embedded chat box. This is the at-a-glance
  view: what the asset looks like right now, what the agent has drawn, and a place
  to ask a question.
- **Telemetry** holds the detail: the KPI tiles, the trend charts (the first
  promoted to a larger "hero" chart), the alarm banner, the event log, and any
  finite-state-machine panel.
- **Agents** shows the multi-agent system working — covered below.

You do not arrange any of this. The client routes each panel from the derived
spec into the region and tab where it belongs, by `kind`, so any twin's spec lays
out sensibly regardless of how its panels are ordered.

---

## The specification is the contract

The derived dashboard is a normal Python object you can inspect and change before
mounting it. `derive_default_spec(config)` returns a `DashboardSpec`; adjust it,
then hand it to `mount_visualization` as the `spec` argument:

```python
from dyon.visualization import derive_default_spec, mount_visualization

spec = derive_default_spec(config)
spec.voice_enabled = True              # turn on the microphone button
spec.asset_name = "Plant A — Pump 001"

mount_visualization(app, config, registry, spec=spec)
```

Because the dashboard is just data, you can reorder panels, drop ones you do not
want, or add your own. Every panel is a `PanelSpec` with a `kind` (`kpi`,
`chart`, `alarms`, `events`, `fsm`, `chat`, `scene`, or `html`) and a small
config dictionary. The client renders each kind from a registry of components,
and you can override or extend that registry from the browser side too
(`DyonDash.register("kpi", fn)`) — but for most twins, deriving the spec and
nudging a few fields is all you will ever do.

---

## Live updates come from the event bus

The dashboard's tiles, charts, and 3D view move because the framework bridges the
twin's **event bus** straight to the browser. Every layer in this guide publishes
domain events — the telemetry router emits `telemetry.routed` on every reading,
the control tier emits state changes — and the visualization layer subscribes
to the bus and forwards those events to the browser over Server-Sent Events. The
client opens one persistent connection to `/api/viz/stream` and updates in place
as events arrive, reconnecting on its own if the connection drops.

This is the same SSE mechanism chapter 06 described for chat, now carrying live
telemetry, and it is completely generic: it works for *any* twin's events because
it keys off the event bus every twin already has. You wrote no streaming code to
get a live dashboard — publishing events, which your layers already do, is the
whole of the contract. When the dashboard first loads it also asks
`/api/viz/history` for recent readings so the charts open already populated, then
keeps them current from the live stream.

---

## The conversational interface

The chat — embedded on the Home tab and available everywhere through the dock —
posts to a single `/api/chat` endpoint backed by one conversational agent, so an
operator can ask "why is the temperature climbing?" and read the agent's
reasoning streamed back token by token. Two things make it more than a text box.

**It can draw.** The framework ships a ready-made chat agent for exactly this
role. `make_dashboard_chat_agent` builds a diagnostic agent (chapter 08) already
equipped with chart and forecast tools, so it answers in prose *and* draws charts
when asked. Hand it to the dashboard and you are done:

```python
from dyon.services.api import create_app
from dyon.visualization import make_dashboard_chat_agent

chat_agent = make_dashboard_chat_agent(
    config, ts_store=ts_store, doc_store=doc_store, knowledge_graph=kg,
)
app = create_app(config, registry, include_viz=True, chat_agent=chat_agent)
```

`create_app` mounts that agent on `/api/chat` exactly once, whether or not the
plain chat endpoint is also enabled. Leave `chat_agent` off and the chat still
works — it falls back to the twin's highest-priority diagnostic agent — but a chat
that draws is one argument away.

Under the hood the agent reaches for two tools, `make_chart_tool` and
`make_forecast_tool`, which you can also add to a custom agent's toolset directly
when you are building your own. When the agent calls one, it returns a chart
specification wrapped in markers the dashboard recognises; the chat panel lifts
the chart out of the stream and renders it inline in the conversation, and
**mirrors it onto the Home tab's agent canvas** so the asset's visuals accumulate
in one place, separate from the chat transcript. `make_chart_tool` plots recent
history for the fields the user named; `make_forecast_tool` projects a field
forward using the forecasting backend from chapter 05. The agent decides when a
chart answers the question better than words.

**It can listen and speak.** Set `voice_enabled = True` on the spec and the chat
grows a microphone button. By default the voice path runs entirely in the browser
using the Web Speech API — speech-to-text for what the operator says,
text-to-speech for the twin's reply — so it needs no server-side install and no
extra dependency. If you want server-side transcription instead (for browsers
without Web Speech, or to control the model), install the `voice` extra and
register a `VoiceProvider`; the `/api/viz/voice/*` endpoints then handle it, and
return a clear "not enabled" response when no provider is registered.

---

## Watching the agents work

The reasoning tier (chapter 08) runs a multi-agent system: each agent
observes, reasons over the live data, and acts on its own interval. The dashboard
surfaces that activity natively. When a multi-agent system is attached — passed
to `mount_visualization(mas=...)`, or discovered from the service registry — the
**Agents** tab polls `/api/viz/agents` and renders a live card per agent showing,
for each one:

- its name, domain, and the severity of its latest assessment,
- the action it decided on (and a flag when it found an anomaly),
- a short summary of its reasoning,
- the tool calls it made, with their inputs and (truncated) outputs.

This is generic: any twin with a `MultiAgentSystem` gets the Agents tab with no
extra work, because the endpoint reads each agent's latest observe→reason→act
snapshot from the system. It turns the agents from a background process into
something an operator can watch and trust.

---

## The 3D viewport

For an asset you have a 3D model of, the dashboard shows that model on the Home
tab and drives it from live readings. The `scene` panel is built from the same
field bindings as everything else, so a hotspot on the model inherits the field's
unit and thresholds and lights up on exactly the same warn/critical logic as its
KPI tile. You build a scene from the config and add it as a panel:

```python
from dyon.visualization import scene_from_config, derive_default_spec
from dyon.visualization.schema import PanelSpec

spec = derive_default_spec(config)
scene = scene_from_config(
    config,
    model_url="/static/pump.glb",
    positions={"bearing_temp_c": "0m 0.4m 0m"},   # where each hotspot sits
    stress_field="bearing_temp_c",                # drives the live colour wash
)
spec.scene_enabled = True
spec.panels.insert(0, PanelSpec(
    id="model", kind="scene", title="Pump", span=2, config=scene.model_dump(),
))

mount_visualization(app, config, registry, spec=spec)
```

**The model responds to the asset's state.** Name a `stress_field` and the
viewport applies a live colour wash to the model that ramps from healthy to
stressed as that field moves between its bounds — a pump casing reddening as its
bearing heats, a turbine blade darkening as fouling builds. When the field carries
no thresholds of its own (a normalised wear index, say), set
`stress_warn`/`stress_crit`/`stress_direction` on the scene to fix the ramp. If
you have distinct models for distinct conditions, `stage_models` swaps the whole
model at chosen thresholds. Hotspot labels track their fields' values, and only
hotspots you have given a position are drawn in 3D, so labels never pile up.

The viewport is **compute-gated by design**. 3D rendering is the one part of the
dashboard that asks something of the viewer's machine, so the client checks for
WebGL2 before it loads the 3D renderer. On a machine that can run it, the operator
gets the live model. On one that cannot — a low-powered tablet on a plant floor,
say — the panel falls back to a 2D schematic you supply as `fallback_svg`, bound
to the same fields, so the information is never lost. You can also run with no 3D
model at all (`model_url=None`) and rely on the schematic alone, which is often
the right call for an asset with no meaningful geometry.

A note on fidelity: a colour wash and a stage swap make the model *reflect* the
asset's state, but a model that physically deforms with the asset — a belt that
visibly slackens, a structure that flexes — needs a rigged asset with the
corresponding morph targets or animations, which you supply and drive through the
same `stress_field`. The framework gives you the live binding; the realism of the
geometry is a property of the model you bring.

---

## Combined twins: one dashboard for many

Chapter 10 showed how twins combine — an **aggregate** rolls a fleet up, a
**collection** compares peers, a **composite** nests twins under boundary
conditions, and a **network** wires them by typed relationships. The dashboard
has a first-class view for each. A combined dashboard carries, alongside the
usual fields, the **members** that make it up and the **topology** between them,
and the client renders an **Overview** tailored to the combination:

- **aggregate** → a fleet roll-up (merged mean/min/max KPIs and average health),
- **collection** → a peer comparison (health ranking and statistical outliers),
- **composite** → the hierarchy and a boundary-condition flow diagram,
- **network** → the typed relationship graph.

Every type also shows a live status card per member; the sidebar lists the
members, and selecting one **drills into that member's own full dashboard** —
its tabs, charts, agents, and 3D scene — federated straight from that member's
API. You build the combined spec from the members and topology:

```python
from dyon.visualization import (
    derive_combined_spec, MemberRef, TopologyEdge,
)

spec = derive_combined_spec(
    combination="composite",
    asset_id="station-01", asset_name="Power Station",
    members=[
        MemberRef(id="boiler",  name="Boiler",  asset_type="boiler",  api_base="/boiler"),
        MemberRef(id="turbine", name="Turbine", asset_type="turbine", api_base="/turbine"),
    ],
    hierarchy={"station-01": ["boiler", "turbine"]},
    edges=[TopologyEdge(source="boiler", target="turbine", kind="flow",
                        label="steam flow")],
)
```

If the combined twin is a real collection twin (chapter 10),
`combined_spec_from_twin(twin, member_api_bases=...)` reads its members,
hierarchy, boundaries, and relationships for you.

**Where does a combined dashboard run?** Each member is a twin that serves its
own `/api/viz/*`, and the combined client federates them in the browser. That
gives you two clean options. The first is to serve each member as its own process
and point a thin combined launcher at their URLs — `create_combined_dashboard_app(spec)`
serves the overview and federates members over CORS, holding no state itself. The
second, when the combination is itself a real twin with its own agents — a
composite overseer, say — is to **bundle** everything in one process: serve the
overseer's own dashboard, agents, and chat at the root, and mount each member
under its own path with `mount_visualization(..., base_path="/boiler")` so the
browser federates them same-origin. In the bundled case the overseer's own
multi-agent system shows up in the Overview's Agents tab, exactly as a single
twin's does. Neither arrangement is privileged: a composite twin can be served
from one process during development and split across several in production
without the dashboard, or its spec, changing at all.

---

## Optional backends

Two dashboard features lean on optional dependencies, and both degrade cleanly
when the dependency is absent:

| Feature                     | Install         | Without it                                  |
|-----------------------------|-----------------|---------------------------------------------|
| Forecast chart / tool       | `dyon[forecast]`| the forecast endpoint returns "not enabled" |
| Server-side voice           | `dyon[voice]`   | voice falls back to the browser's Web Speech|

The dashboard client itself, the live charts, the chat panel, the agents view,
and the 3D viewport need nothing beyond the framework — they render in the browser
from pinned CDN libraries, so there is no build step and no front-end toolchain to
install. The `/api/viz/capabilities` endpoint reports which optional features are
live, and the dashboard hides or disables what is not available rather than
failing.

---

## Where this leaves you

The dashboard is the framework's account of itself made visible. Every panel is
driven by something an earlier chapter built — sensor fields become tiles, charts,
and 3D hotspots; the event bus drives the live updates; the control tier's
thresholds become alarms and the model's colour wash; the diagnostic agent answers
in the chat and draws on the canvas; the multi-agent system shows its work in the
Agents tab; and combined twins compose all of it into one federated view. You did
not write a front-end to get any of it; you declared an asset, and the framework
drew it.
