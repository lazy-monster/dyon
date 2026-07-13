"""The visualization schema is the rendering contract: it must round-trip
losslessly through JSON and emit a JSON Schema (so the client and any external
tooling can validate against it)."""

from __future__ import annotations

from dyon.visualization.schema import (
    DEFAULT_THEME,
    AlarmRule,
    ChartSpec,
    DashboardSpec,
    FieldBinding,
    KpiSpec,
    PanelSpec,
)


def test_dashboard_spec_round_trips_through_json():
    spec = DashboardSpec(
        asset_id="a1",
        asset_name="Asset One",
        asset_type="pump",
        panels=[
            PanelSpec(
                id="kpi-temp", kind="kpi", title="Temp",
                config=KpiSpec(
                    id="kpi-temp",
                    binding=FieldBinding(field="temp", unit="C", warn=70, crit=90),
                ).model_dump(),
            ),
            PanelSpec(
                id="chart-0", kind="chart",
                config=ChartSpec(id="chart-0", fields=["temp"]).model_dump(),
            ),
        ],
        alarms=[AlarmRule(field="temp", level="warn", threshold=70, direction="above")],
    )
    restored = DashboardSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec


def test_default_theme_applied_when_unspecified():
    spec = DashboardSpec(asset_id="a", asset_name="A", asset_type="t")
    assert spec.theme == DEFAULT_THEME
    # The default must be a copy, not the shared module-level dict.
    spec.theme["--bg"] = "#ffffff"
    assert DEFAULT_THEME["--bg"] == "#0f1117"


def test_dashboard_spec_emits_json_schema():
    schema = DashboardSpec.model_json_schema()
    assert schema["title"] == "DashboardSpec"
    assert "panels" in schema["properties"]


def test_chart_spec_accepts_full_vega_lite_object():
    vl = {"mark": "line", "encoding": {"x": {"field": "t"}, "y": {"field": "v"}}}
    chart = ChartSpec(id="c", vega_lite=vl, source="inline")
    assert chart.vega_lite == vl
    assert ChartSpec.model_validate_json(chart.model_dump_json()).vega_lite == vl
