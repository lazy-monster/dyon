"""``derive_default_spec`` turns a twin's config into a usable dashboard with no
authoring effort. These tests pin the mapping: one KPI per field, grouped charts
by unit, alarm rules with the correct direction, and the standard panels."""

from __future__ import annotations

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.visualization.derive import derive_default_spec
from dyon.visualization.schema import DashboardSpec


def _cfg(**fields):
    return TwinConfig(
        asset_id="pump1", asset_name="Pump One", asset_type="centrifugal_pump",
        sensor_fields=list(fields.values()),
    )


def test_one_kpi_panel_per_field():
    cfg = _cfg(
        a=SensorFieldSpec(name="temp", unit="C", nominal=25.0),
        b=SensorFieldSpec(name="humidity", unit="%", nominal=60.0),
    )
    spec = derive_default_spec(cfg)
    kpis = [p for p in spec.panels if p.kind == "kpi"]
    assert {p.id for p in kpis} == {"kpi-temp", "kpi-humidity"}


def test_identity_carried_from_config():
    spec = derive_default_spec(_cfg(a=SensorFieldSpec(name="temp")))
    assert (spec.asset_id, spec.asset_name, spec.asset_type) == (
        "pump1", "Pump One", "centrifugal_pump",
    )


def test_alarm_rules_emitted_with_translated_direction():
    cfg = _cfg(
        hi=SensorFieldSpec(name="temp", warn_threshold=70, crit_threshold=90,
                           threshold_direction="high"),
        lo=SensorFieldSpec(name="moisture", crit_threshold=10,
                           threshold_direction="low"),
    )
    spec = derive_default_spec(cfg)
    by_field = {(r.field, r.level): r for r in spec.alarms}
    assert by_field[("temp", "warn")].direction == "above"
    assert by_field[("temp", "crit")].direction == "above"
    assert by_field[("moisture", "crit")].direction == "below"
    assert ("moisture", "warn") not in by_field   # no warn bound configured


def test_fields_grouped_by_unit_into_charts():
    cfg = _cfg(
        a=SensorFieldSpec(name="temp", unit="C"),
        b=SensorFieldSpec(name="setpoint", unit="C"),
        c=SensorFieldSpec(name="humidity", unit="%"),
    )
    spec = derive_default_spec(cfg)
    charts = [p for p in spec.panels if p.kind == "chart"]
    field_groups = sorted(sorted(p.config["fields"]) for p in charts)
    assert field_groups == [["humidity"], ["setpoint", "temp"]]


def test_standard_panels_present():
    spec = derive_default_spec(_cfg(a=SensorFieldSpec(name="temp")))
    kinds = {p.kind for p in spec.panels}
    assert {"alarms", "events", "chat"} <= kinds
    assert spec.chat_enabled is True
    assert spec.scene_enabled is False
    assert spec.voice_enabled is False


def test_empty_config_produces_valid_spec():
    cfg = TwinConfig(asset_id="x", asset_name="X", asset_type="t", sensor_fields=[])
    spec = derive_default_spec(cfg)
    assert isinstance(spec, DashboardSpec)
    assert [p.kind for p in spec.panels if p.kind == "kpi"] == []
    # Standard panels still render even with no fields.
    assert {"alarms", "events", "chat"} <= {p.kind for p in spec.panels}
