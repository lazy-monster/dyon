"""The 3D scene spec round-trips and its hotspots inherit field units and
thresholds (the data the client colours from)."""

from __future__ import annotations

from vizfakes import make_config

from dyon.visualization.scene import (
    binding_to_hotspot,
    build_scene_spec,
    scene_from_config,
)
from dyon.visualization.schema import FieldBinding, SceneSpec


def test_scene_spec_round_trips():
    spec = build_scene_spec(
        model_url="asset.glb",
        bindings=[FieldBinding(field="temp", unit="C", warn=70, crit=90)],
        positions={"temp": "0m 1m 0m"},
    )
    restored = SceneSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec


def test_binding_maps_to_hotspot_with_thresholds():
    b = FieldBinding(field="temp", label="Temp", unit="C", warn=70, crit=90, direction="above")
    h = binding_to_hotspot(b, "0m 1m 0m")
    assert h == {
        "field": "temp", "label": "Temp", "unit": "C",
        "warn": 70, "crit": 90, "direction": "above", "position": "0m 1m 0m",
    }


def test_build_scene_spec_emits_one_hotspot_per_binding():
    spec = build_scene_spec(
        model_url="m.glb",
        bindings=[FieldBinding(field="a"), FieldBinding(field="b")],
        positions={"a": "0 0 0"},
    )
    assert [h["field"] for h in spec.hotspots] == ["a", "b"]
    # Field without a supplied position still carried (empty position string).
    assert spec.hotspots[1]["position"] == ""


def test_scene_from_config_inherits_field_metadata():
    spec = scene_from_config(make_config(), model_url="asset.glb")
    by_field = {h["field"]: h for h in spec.hotspots}
    assert by_field["temp"]["warn"] == 70.0
    assert by_field["temp"]["crit"] == 90.0
    assert by_field["moisture"]["direction"] == "below"   # threshold_direction="low"


def test_scene_without_model_supports_fallback_only():
    spec = build_scene_spec(model_url=None, fallback_svg="<svg/>")
    assert spec.model_url is None
    assert spec.fallback_svg == "<svg/>"
