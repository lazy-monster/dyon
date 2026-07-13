"""Combined-twin dashboards: ``derive_combined_spec`` builds a federated spec,
and ``combined_spec_from_twin`` bridges any collection twin (aggregate /
collection / composite / network) to one by reading its ids and topology."""

from __future__ import annotations

from dataclasses import dataclass, field

from dyon.visualization.derive import combined_spec_from_twin, derive_combined_spec
from dyon.visualization.schema import DashboardSpec, MemberRef, TopologyEdge


def test_derive_combined_spec_is_federated_not_paneled():
    spec = derive_combined_spec(
        combination="composite",
        asset_id="station",
        asset_name="Power Station",
        members=[
            MemberRef(id="turbine", name="Turbine", api_base="http://h:8502"),
            MemberRef(id="boiler", name="Boiler", api_base="http://h:8503"),
        ],
        hierarchy={"station": ["turbine", "boiler"]},
    )
    assert isinstance(spec, DashboardSpec)
    assert spec.combination == "composite"
    assert {m.id for m in spec.members} == {"turbine", "boiler"}
    # The overview is rendered client-side from members + topology; nothing is
    # paneled server-side for a combined twin.
    assert spec.panels == []
    assert spec.hierarchy == {"station": ["turbine", "boiler"]}


# --- duck-typed stand-ins for the runtime collection twins -------------------
@dataclass
class _BC:
    source_twin: str
    target_twin: str
    source_field: str = "out"
    target_field: str = "in"


@dataclass
class _Rel:
    source_twin: str
    target_twin: str
    relationship_type: str = "feeds"


@dataclass
class _Composite:
    collection_type: str = "composite"
    collection_id: str = "station"
    component_ids: list = field(default_factory=lambda: ["turbine", "boiler"])
    hierarchy: dict = field(default_factory=lambda: {"station": ["turbine", "boiler"]})
    boundaries: list = field(
        default_factory=lambda: [_BC("boiler", "turbine", "steam_kg_s", "steam_inlet")]
    )


@dataclass
class _Network:
    collection_type: str = "network"
    collection_id: str = "field_net"
    component_ids: list = field(default_factory=lambda: ["a", "b", "c"])
    relationships: list = field(default_factory=lambda: [
        _Rel("a", "b", "upstream_of"), _Rel("b", "c", "feeds"),
    ])


def test_combined_spec_from_composite_twin_maps_hierarchy_and_flows():
    spec = combined_spec_from_twin(
        _Composite(),
        member_api_bases={"turbine": "http://h:8502", "boiler": "http://h:8503"},
        member_names={"turbine": "Turbine", "boiler": "Boiler"},
    )
    assert spec.combination == "composite"
    assert spec.asset_id == "station"
    # api_base wired through; parent derived from the hierarchy
    turbine = next(m for m in spec.members if m.id == "turbine")
    assert turbine.api_base == "http://h:8502" and turbine.parent == "station"
    # boundary condition becomes a "flow" edge labelled by the target field
    flows = [e for e in spec.edges if e.kind == "flow"]
    assert flows and flows[0].source == "boiler" and flows[0].target == "turbine"
    assert flows[0].label == "steam_inlet"


def test_combined_spec_from_network_twin_maps_relationships():
    spec = combined_spec_from_twin(
        _Network(),
        member_api_bases={"a": "http://h:1", "b": "http://h:2", "c": "http://h:3"},
    )
    assert spec.combination == "network"
    rels = [e for e in spec.edges if e.kind == "relationship"]
    assert {(e.source, e.target, e.label) for e in rels} == {
        ("a", "b", "upstream_of"), ("b", "c", "feeds"),
    }
    # name auto-derived from id when none supplied
    assert next(m for m in spec.members if m.id == "a").name == "A"


def test_member_ref_round_trips_through_dashboard_spec_json():
    spec = derive_combined_spec(
        combination="network", asset_id="n", asset_name="Net",
        members=[MemberRef(id="a", name="A", api_base="http://h:1")],
        edges=[TopologyEdge(source="a", target="a", kind="relationship", label="self")],
    )
    reloaded = DashboardSpec.model_validate(spec.model_dump())
    assert reloaded.members[0].api_base == "http://h:1"
    assert reloaded.edges[0].kind == "relationship"
