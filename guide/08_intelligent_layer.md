# 08 — The Agent Layer: Reasoning

The control tier can tell you *that* a reading crossed a line. This tier tells you
*why*. It is slower — seconds to minutes rather than milliseconds — because it
reasons about the meaning of the data rather than its bare value, and that is
exactly what some faults require. A combination of three readings that are each
individually normal, a trend only visible against last month's, a situation whose
significance depends on context no rule author foresaw: rules cannot catch these,
but a reasoning agent armed with domain knowledge can.

The tier has two halves that work together. A **knowledge graph** holds your
asset's domain expertise — its components, the ways it fails, and what to do about
each. A **diagnostic agent** wraps a large language model with tools that read the
twin's live data and query that graph. The graph supplies facts specific to your
asset that a general-purpose LLM was never trained on; the LLM supplies language,
reasoning, and the judgement to decide which facts it needs.

---

## The knowledge graph

A knowledge graph stores relationships between concepts as first-class objects,
not rows in tables. Instead of recording a temperature reading and writing code to
join it to a fault, you store the relationship itself —
`(Symptom: high_temperature) <-[:CAUSES]- (FailureMode: overheating)
-[:REQUIRES]-> (MaintenanceAction: check_cooling)` — and then traverse it: from the
symptom a reading triggers, follow the edges to every failure mode it implies, the
components each affects, and the actions each calls for. That multi-hop traversal
is awkward to express as SQL joins or as rules, and natural as a graph.

The framework's graph models four kinds of thing and the links between them:

- **Components** — the physical parts of the asset (bearing, seal, impeller);
- **Failure modes** — what can go wrong (overheating, cavitation, wear);
- **Symptoms** — the sensor signals that point to a failure mode;
- **Maintenance actions** — what a technician should do in response.

You declare all of this as plain Python dataclasses; the framework translates them
into Neo4j for you, so you never write Cypher for the standard pattern:

```python
from dyon.intelligent import KnowledgeGraph, KnowledgeGraphSpec, FailureMode, SymptomMapping
from neo4j import GraphDatabase

driver = GraphDatabase.driver(config.neo4j.uri,
                              auth=(config.neo4j.user, config.neo4j.password))

spec = KnowledgeGraphSpec(
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
        # A symptom triggers when its field crosses the threshold in the given direction.
        SymptomMapping(symptom_name="high_temperature", sensor_field="temperature_c",
                       threshold=60.0, direction="high",
                       failure_modes=["overheating", "bearing_wear"]),
        SymptomMapping(symptom_name="low_pressure", sensor_field="pressure_bar",
                       threshold=3.5, direction="low",
                       failure_modes=["cavitation"]),
        SymptomMapping(symptom_name="high_vibration", sensor_field="vibration_mm_s",
                       threshold=2.5, direction="high",
                       failure_modes=["bearing_wear", "cavitation"]),
    ],
)

kg = KnowledgeGraph(config, driver)
kg.setup_from_spec(spec)   # builds the graph; idempotent, safe to re-run
```

You can query the graph directly. Given current readings, it returns the active
symptoms; given symptoms, it returns the implicated failure modes with their
actions, ranked so the most severe come first:

```python
readings = {"temperature_c": 72.0, "pressure_bar": 3.2, "vibration_mm_s": 1.2}

kg.diagnose_from_readings(readings)
# ['high_temperature', 'low_pressure']

kg.diagnose(['high_temperature', 'low_pressure'])
# [{'failure': 'overheating', 'severity': 'high',
#   'actions': ['check_cooling_system', 'inspect_bearing']},
#  {'failure': 'cavitation', 'severity': 'high',
#   'actions': ['check_inlet_pressure', 'reduce_speed']}]

kg.get_components()
# ['bearing', 'seal', 'impeller', 'motor', 'shaft']
```

If your domain needs node types beyond this equipment-health pattern — routes,
depots, suppliers — add raw Cypher through the spec's `custom_cypher` field,
which runs after the standard schema is built. A logistics twin, for instance,
can use this to add `Depot` and `Route` nodes:

```python
spec = KnowledgeGraphSpec(
    components=[...], failure_modes=[...], symptom_mappings=[...],
    custom_cypher=[
        "MERGE (:Depot {code: 'DEP-04'})",
        "MERGE (:Route {name: 'north_loop'})",
    ],
)
```

Every statement uses `MERGE`, which is idempotent, so the graph stays consistent
across restarts.

---

## The diagnostic agent

On its own, a language model is just text in, text out — it knows nothing about
your sensors right now. An *agent* fixes that by giving the model **tools**: Python
functions it can choose to call mid-reasoning. You ask a question; the model
decides it needs data and calls a tool; the framework runs the function and hands
back the result; the model reads it, decides whether it needs more, and eventually
writes its answer. The model directs the information-gathering instead of being
handed everything up front, which is both cheaper and easy to extend — add a tool
and the model can use it without any retraining.

`DiagnosticAgent` comes with five tools wired to the twin's live stores:

| Tool                  | What it returns                                       |
|-----------------------|-------------------------------------------------------|
| `get_twin_state`      | the full Ditto Thing                                  |
| `get_sensor_readings` | the latest value of every sensor field                |
| `diagnose_asset`      | active symptoms and failure modes from the graph      |
| `get_recent_events`   | the last N events from MongoDB                         |
| `get_asset_components`| the asset's components from the graph                 |

You build it from the config — `build_llm()` reads your `DT_LLM__*` settings and
returns the right LangChain model — and hand it the stores and graph it needs:

```python
from dyon.intelligent import DiagnosticAgent
from dyon.intelligent.agent import build_llm

agent = DiagnosticAgent(config, llm=build_llm(config),
                        ditto_client=ditto, ts_store=ts, doc_store=doc,
                        knowledge_graph=kg)

answer = await agent.ask("Why is the pump running hot?")
# "Temperature is 72 °C, above the 60 °C warning threshold. The knowledge graph
#  links this to two failure modes — overheating (high) and bearing wear (medium).
#  Recommended actions: check the cooling system and inspect the bearing."
```

The agent chooses which tools to call based on the question, so the same agent
handles "Is anything wrong?", "What maintenance should I schedule?", and "How does
current vibration compare to the last day?"

Every call the agent makes is bounded, because an LLM provider is a remote
service that can hang, ramble, or flake, and a twin must keep running when it
does. Three `DT_LLM__*` settings enforce this: `timeout_s` caps each request
(default 60 s), `max_tokens` caps each reply (default 2048 — also your cost
ceiling per call), and `max_retries` absorbs transient provider errors (default
2). On top of the per-request timeout, `ask()` itself has a hard deadline
covering the whole tool-calling loop, so even a pathological conversation ends
with an error string rather than a stuck coroutine. A slow provider therefore
costs you one answer, never the layer.

One boundary is deliberate and worth stating plainly: the agent is a **diagnostician,
not an actuator**. It can read data and advise, but it cannot open a valve, publish
a control command, or change the state machine. Acting on its findings is the job
of the governance tier in chapter 09, which keeps every consequential action
behind one place that can enforce safety limits — rather than letting several
agents reach for the controls independently.

### How an agent thinks: observe, reason, act

A `TwinAgent` works in three steps that mirror a careful analyst. `observe()`
gathers raw facts — sensor values, recent history, the FSM state — without drawing
conclusions. `reason()` interprets them, typically by calling the LLM, and
produces findings. `act()` carries out consequences, such as logging or alerting.

The split matters because the steps run on different conditions. `observe()` and
`reason()` run every cycle, so an agent always has a current read on the situation
to show a dashboard, even when all is well. `act()` runs only when `observe()`
reports an anomaly, so the side effects fire only when they should. The stock
`DiagnosticAgent` observes passively (it never flags an anomaly by itself); you
add detection by subclassing it:

```python
class ActiveDiagnosticAgent(DiagnosticAgent):
    async def observe(self) -> dict:
        readings = {f: self._ts_store.get_latest(f) for f in self.config.field_names}
        symptoms = self.kg.diagnose_from_readings(readings)
        return {"anomaly_detected": len(symptoms) > 0,
                "symptoms": symptoms, "readings": readings}
```

For the MAS (below) to record an agent's status well, have `reason()` return
`action`, `severity`, and `summary` keys alongside anything domain-specific.

### Adding your own tools

To give an agent a tool the framework cannot provide — a call into a domain
simulator, a query to an external service — override `_build_extra_tools()`. It is
invoked while the agent is being built, so any state the tool needs must be set on
`self` *before* you call `super().__init__()`:

```python
from langchain_core.tools import tool as lc_tool

class PumpDiagnosticAgent(DiagnosticAgent):
    def __init__(self, *args, curve_model=None, **kwargs):
        self._curve = curve_model            # set before the parent compiles tools
        super().__init__(*args, **kwargs)

    def _build_extra_tools(self) -> list:
        curve = self._curve
        @lc_tool
        def expected_head(flow_lpm: float) -> dict:
            """Return the pump's expected head for a given flow rate."""
            return {"head_m": curve.head_at(flow_lpm)}
        return [expected_head]
```

---

## Running agents continuously: the MultiAgentSystem

A single `ask()` answers one question. To have the reasoning tier watch the
asset on its own, wrap your agents in a `MultiAgentSystem` and return it from
`build_layers()`:

```python
from dyon.intelligent import MultiAgentSystem

"intelligent": MultiAgentSystem(self.config, self.bus,
                                agents=[agent],
                                monitor_interval=30,   # run the loop every N seconds
                                max_concurrent=1,      # parallel LLM calls allowed
                                cache=cache,           # live status in Redis
                                doc_store=doc),        # durable findings in MongoDB
```

On each cycle the MAS runs every agent's `observe()` and `reason()`, and calls
`act()` for any agent whose observation flagged an anomaly — also publishing an
`agent.action` event when it does. Agents are tried in priority order, highest
first.

`max_concurrent` caps how many agents may call the LLM at once. The default of `1`
serialises them, which keeps a local model (Ollama) or a rate-limited API from
being overwhelmed; raise it only when your provider can take the load.

The two stores serve different needs and can be used together. With `cache`, the
MAS writes a compact status record to Redis under `mas_agent_<agent_name>` on
*every* cycle, so a dashboard always sees the latest:

```json
{"agent_name": "diagnostic", "domain": "general", "anomaly": true,
 "action": "recommend_inspection", "severity": "warning",
 "detail": "temperature 72 °C, overheating likely", "ts_s": 1748400000}
```

With `doc_store`, it writes to MongoDB only when a cycle is *noteworthy* — an
anomaly, or an action other than routine monitoring — recording both that compact
status (`mas_agent_<name>`) and a fuller record with observations, findings, and
tool calls (`mas_agent_detail_<name>`). Routine cycles are not persisted, so the
history stays clean and queryable:

```python
doc.get_events_by_type("mas_agent_diagnostic", n=100)
doc.get_events_by_type("mas_agent_detail_diagnostic", n=100)
```

### Responding to escalations immediately

The MAS does not only run on its timer. When it starts, it subscribes to the
`reactive.escalation_requested` event from chapter 07. The moment the control
tier's state machine enters a warning or critical state, that event arrives
carrying a ready-made investigation question, and the MAS routes it straight to
its highest-priority agent instead of waiting for the next polling cycle. The
agent's answer is logged to MongoDB as `mas_escalation_response`. This is the
chain end to end:

```
FSM enters a warning/critical state
        → reactive.escalation_requested (with a question)
        → MAS routes it to the top agent
        → the agent investigates with its tools
        → the answer is logged as mas_escalation_response
        → the governance tier reads it on its next cycle
```

### Feeding the governance tier

After every cycle the MAS keeps each agent's full observation and findings in
memory, reachable by name. The governance tier reads this during its own
observe phase to fold the agents' diagnoses into its decisions:

```python
detail = mas.get_agent_detail("diagnostic")   # latest observations + findings
```

It can also pose a fresh question to a specific agent on demand, without
disturbing the monitoring loop:

```python
answer = await mas.ask_agent("diagnostic",
                             "Is the rising vibration consistent with bearing wear?")
```

---

## Choosing and skipping pieces

The LLM is chosen entirely through configuration — no code change moves you
between providers:

```bash
DT_LLM__PROVIDER=anthropic        # openai | anthropic | ollama | offline
DT_LLM__MODEL=claude-sonnet-4-6
DT_LLM__API_KEY=sk-ant-...
# Ollama needs no key:  DT_LLM__PROVIDER=ollama, DT_LLM__BASE_URL=http://localhost:11434
# No model at all:      DT_LLM__PROVIDER=offline answers in-process, no key, no network
```

The `offline` provider is the zero-dependency floor: `OfflineChatModel`
(`dyon.intelligent`) is a real chat model that answers from inside the process and
accepts tool binding, so an agent graph builds and runs identically online or off.
It is for demos, tests, and air-gapped runs — pass a `responder` to give it a
domain voice. It constructs and responds; it does not reason, so treat it as a way
to exercise every layer without a key, not a substitute for a model.

And if you want an agent without a knowledge graph, construct a `KnowledgeGraph`
and simply never call `setup_from_spec`. Its `diagnose_asset` tool reports no
symptoms and `get_asset_components` returns an empty list, while everything else —
the sensor and event tools, the reasoning — works as normal.

---

The twin can now explain itself. The final active layer takes the next step: it
reads these explanations and decides what the asset should actually do.
