# 10 — Connectors and Collection Twins

Every chapter so far has built one twin in isolation. Real systems are rarely one
asset, though — a fleet of pumps, a plant where one stage feeds the next, a water
network where a failure ripples downstream. This chapter covers the two tools for
working with many twins at once: **connectors**, which let one twin reach another,
and **collection twins**, which treat a whole group as a single twin built on top
of those connectors.

---

## Connectors: how twins reach each other

A connector is a transport between twins. The framework ships three, each
operating at a different layer and suited to a different need:

| Connector       | Layer         | Mechanism                                   | Use it to                       |
|-----------------|---------------|---------------------------------------------|---------------------------------|
| `DittoConnector`| `services`    | HTTP to another twin's Eclipse Ditto record | read another twin's state       |
| `MQTTConnector` | `data`        | publish/subscribe on a shared MQTT broker   | stream data between twins       |
| `APIConnector`  | `intelligent` | HTTP to another twin's `/api/chat`          | ask another twin's agent        |

You collect a twin's connectors in a `ConnectorRegistry`, registering each under
the layer it exposes:

```python
from dyon.connector import ConnectorRegistry, DittoConnector, MQTTConnector, APIConnector

registry = ConnectorRegistry(config)
registry.register(DittoConnector(config))                                  # read peers' state
registry.register(MQTTConnector(config, known_twins=["pump_002", "pump_003"]))  # stream to peers
registry.register(APIConnector({"pump_002": "http://localhost:8502",       # ask peers' agents
                                "pump_003": "http://localhost:8503"}))
```

To use one, ask the registry for a route to the target twin at the layer you need,
then call `query` (request a response) or `push` (send one-way):

```python
# read pump_002's telemetry through Ditto
conn = registry.find_route("pump_002", layer="services")
telemetry = await conn.query("pump_002", {"feature": "telemetry"})

# push a value into pump_002's data layer over MQTT
conn = registry.find_route("pump_002", layer="data")
await conn.push("pump_002", {"external_pressure": 3.9})

# ask pump_002's diagnostic agent a question
conn = registry.find_route("pump_002", layer="intelligent")
answer = await conn.query("pump_002", {"question": "Are you experiencing any faults?"})
```

The choice of layers is deliberate. A twin exposes its *data stream* (Data,
through MQTT), its *state* (Services, through Ditto), and its *reasoning* (the
Agent layer, through the chat API — the registry keys it by the module name,
`intelligent`). It exposes nothing else, and the two omissions are the point.

The Simulation and Model layer is never exposed, because a peer that reached into
it would be coupled to the internals of your model; a peer that needs a prediction
asks the Services layer for one instead. And the Agent layer exposes its reasoning
interface but never its control surface, since letting one twin trip another's
deterministic control would be unsafe, and anything worth knowing about that
control is already published through Ditto's `health` and `telemetry` features.
Sharing state and reasoning, but never raw models or control, is what keeps one
twin from reaching in and destabilising another.

---

## Collection twins: many twins as one

A collection twin does not model a physical asset. It is a coordinator that sits
above a group of component twins, reaches them through connectors, and makes
group-level decisions. There are four patterns, each matching a different way
twins relate to one another:

| Pattern        | Use when                                                   | Example                          |
|----------------|------------------------------------------------------------|----------------------------------|
| `AggregateDT`  | the twins are identical and you want one fused state        | a fleet of identical pumps       |
| `CollectionDT` | the twins are alike but you want each tracked individually  | a bank of sensors                |
| `CompositeDT`  | one twin's output feeds the next                            | boiler → turbine → generator     |
| `NetworkDT`    | the twins are linked physically and failures propagate      | a water distribution network     |

All four share the same lifecycle: you construct them with the component IDs and a
connector registry, and call `run(interval=...)` to drive their orchestration loop.

### AggregateDT — one fused view

`AggregateDT` fuses identical twins into a single state. Each cycle it reads every
member's health and telemetry and reports the averages, the worst case, and how
many members it could reach:

```python
from dyon.collection import AggregateDT

fleet = AggregateDT(collection_id="pump_fleet_001", config=fleet_config,
                    component_twin_ids=["pump_001", "pump_002", "pump_003"],
                    connector_registry=registry, ditto_client=ditto)

state = await fleet.aggregate_state()
# {"avg_health": 87.5, "min_health": 62.0, "worst_state": "warning",
#  "member_count": 3, "active_count": 3,
#  "telemetry_summary": {"temperature_c": {"mean": 48.2, "min": 44.1, "max": 72.0}, ...}}

results = await fleet.broadcast_command({"setpoint_change": {"pressure_bar": 4.5}})
# {"pump_001": True, "pump_002": True, "pump_003": False}
```

It publishes its fused state to its own Ditto feature, and raises an
`aggregate.critical` event when the worst member has shut down.

### CollectionDT — a group with individual identity

`CollectionDT` keeps each member distinct, which is what you want when the point is
to find the odd one out. It offers outlier detection and health ranking over the
group:

```python
from dyon.collection import CollectionDT

bank = CollectionDT(collection_id="sensor_bank_a", config=group_config,
                    component_twin_ids=[f"sensor_{i:03d}" for i in range(1, 51)],
                    connector_registry=registry)

outliers = await bank.find_outliers("temperature_c", z_threshold=2.0)
# ['sensor_017', 'sensor_031'] — members more than 2 std-devs from the group mean

ranking = await bank.rank_by_health()
# [('sensor_017', 42.0), ('sensor_031', 58.0), ...] — worst first
```

### CompositeDT — a chain of stages

`CompositeDT` models a system where one stage's output is the next stage's input. A
list of `BoundaryCondition`s declares those data flows, and the composite pushes
each source field into the matching target field every cycle:

```python
from dyon.collection import CompositeDT, BoundaryCondition

plant = CompositeDT(collection_id="power_plant_001", config=plant_config,
                    component_twin_ids=["boiler_001", "turbine_001", "generator_001"],
                    connector_registry=registry,
                    hierarchy={"power_plant_001": ["boiler_001", "turbine_001", "generator_001"]},
                    boundary_conditions=[
                        BoundaryCondition(source_twin="boiler_001",  source_field="steam_pressure",
                                          target_twin="turbine_001", target_field="inlet_pressure"),
                        BoundaryCondition(source_twin="turbine_001",  source_field="shaft_speed_rpm",
                                          target_twin="generator_001", target_field="input_speed_rpm"),
                        # transform the value in transit — by a callable, or "scale"/"offset"
                        BoundaryCondition(source_twin="boiler_001",  source_field="steam_temp_c",
                                          target_twin="turbine_001", target_field="inlet_temp_c",
                                          transform=lambda x: x * 0.95),  # 5% pipe loss
                    ],
                    ditto_client=ditto)
```

A `BoundaryCondition` passes its value through unchanged by default; set `transform`
to a callable, or to `"scale"` (with `transform_factor`) or `"offset"` (with
`transform_offset`), to adjust it in transit. Because the composite delivers
boundary values into the target's data layer, the target twins must be reachable
through a registered data-layer connector.

The composite also supports hot-swapping a stage — after maintenance, say:

```python
await plant.swap_component("turbine_001", "turbine_002_refurbished")
```

By default the swap is validated: the replacement must already expose its Ditto
features and produce every field the boundary conditions read from it, or the swap
raises and leaves the composite untouched.

### NetworkDT — a graph with cascade effects

`NetworkDT` connects twins by typed relationships and reasons about the graph. You
declare the edges, and it can find which failing twin would harm the most others
downstream, and which twin is a single point of failure:

```python
from dyon.collection import NetworkDT, TwinRelationship

network = NetworkDT(collection_id="water_district_north", config=net_config,
                    component_twin_ids=["pump_001", "tank_001", "valve_002", "pump_003"],
                    connector_registry=registry,
                    relationships=[
                        TwinRelationship("pump_001", "tank_001",  "feeds",    {"capacity_lpm": 200}),
                        TwinRelationship("tank_001", "valve_002", "supplies", {"pipe_mm": 150}),
                        TwinRelationship("valve_002","pump_003",  "feeds"),
                    ],
                    neo4j_driver=driver)   # optional: persists the topology in Neo4j

risks = await network.detect_cascade_risk()
# [{"twin_id": "pump_001", "health": 38.0,
#   "downstream_affected": ["tank_001", "valve_002", "pump_003"], "cascade_severity": 3}]

bottlenecks = await network.find_bottlenecks()
# ['tank_001'] — removing it would disconnect the network
```

Each cycle it logs the network's overall health and publishes a
`network.cascade_risk` event for the most severe risk it finds.

---

## Running several collections together

When more than one collection watches the same components — an `AggregateDT` for
the fused view and a `NetworkDT` for cascade analysis — a `CollectionOrchestrator`
runs them concurrently and shuts them down together:

```python
from dyon.collection.orchestrator import CollectionOrchestrator

orchestrator = CollectionOrchestrator(interval=15)
orchestrator.add(fleet)
orchestrator.add(network)
asyncio.run(orchestrator.run_forever())
```

---

You now have every building block the framework offers, from a single sensor
reading up to a coordinated network of twins. The next chapter puts a full
selection of them together in one worked example, end to end.
