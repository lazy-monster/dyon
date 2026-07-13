"""Agent chart tools and the direct ``POST /api/viz/chart`` path share one
builder and produce valid inline Vega-Lite specs."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from vizfakes import VizFakeTimeSeriesStore, make_config, make_registry

from dyon.visualization.agent_tools import (
    CHART_CLOSE,
    CHART_OPEN,
    build_timeseries_chart_spec,
    make_chart_tool,
    parse_chart_query,
)
from dyon.visualization.serve import mount_visualization


def _store():
    import time
    now = time.time()
    return VizFakeTimeSeriesStore(
        latest={"temp": 50.0, "moisture": 30.0},
        history={"temp": [{"ts": now - 60, "value": 49.0}, {"ts": now, "value": 50.0}]},
    )


def test_builder_emits_inline_vega_lite():
    cfg = make_config()
    spec = build_timeseries_chart_spec(_store(), cfg, ["temp"], window_minutes=60)
    assert spec.source == "inline"
    assert spec.fields == ["temp"]
    vl = spec.vega_lite
    assert vl["mark"]["type"] == "line"
    assert vl["data"]["values"][0]["field"] == "temp"


def test_builder_emits_iso_timestamps_not_raw_seconds():
    # Regression: a Vega-Lite ``temporal`` axis reads a bare number as epoch
    # milliseconds, so raw Unix *seconds* collapse every point onto ~1970 (one
    # blue smear). The builder must emit ISO-8601 strings instead.
    cfg = make_config()
    spec = build_timeseries_chart_spec(_store(), cfg, ["temp"], window_minutes=60)
    assert spec.vega_lite["encoding"]["x"]["type"] == "temporal"
    for row in spec.vega_lite["data"]["values"]:
        assert isinstance(row["t"], str), "timestamp must be an ISO string, not a number"
        # Parses as a real date in the present era, not 1970.
        from datetime import datetime
        assert datetime.fromisoformat(row["t"]).year >= 2000


def test_builder_supports_chart_types():
    cfg = make_config()
    for ct, expected in [("bar", "bar"), ("area", "area"),
                         ("point", "point"), ("scatter", "point")]:
        spec = build_timeseries_chart_spec(_store(), cfg, ["temp"], chart_type=ct)
        assert spec.vega_lite["mark"]["type"] == expected
    # An unknown type falls back to a line rather than producing an invalid spec.
    spec = build_timeseries_chart_spec(_store(), cfg, ["temp"], chart_type="bogus")
    assert spec.vega_lite["mark"]["type"] == "line"


def test_builder_applies_single_series_color_and_points():
    cfg = make_config()
    spec = build_timeseries_chart_spec(
        _store(), cfg, ["temp"], color="#22c55e", show_points=True
    )
    assert spec.vega_lite["encoding"]["color"] == {"value": "#22c55e"}
    assert spec.vega_lite["mark"]["point"] is True


def test_builder_normalizes_multi_field_to_unit_range():
    import time
    now = time.time()
    store = VizFakeTimeSeriesStore(
        latest={"temp": 50.0, "moisture": 30.0},
        history={
            "temp": [{"ts": now - 60, "value": 40.0}, {"ts": now, "value": 90.0}],
            "moisture": [{"ts": now - 60, "value": 10.0}, {"ts": now, "value": 50.0}],
        },
    )
    spec = build_timeseries_chart_spec(
        store, make_config(), ["temp", "moisture"], normalize=True
    )
    vals = [r["v"] for r in spec.vega_lite["data"]["values"]]
    assert min(vals) == 0.0 and max(vals) == 1.0
    # Several fields get an automatic colour-by-field encoding.
    assert spec.vega_lite["encoding"]["color"]["field"] == "field"


def test_chart_tool_forwards_styling():
    cfg = make_config()
    tool = make_chart_tool(_store(), cfg)
    out = tool.invoke({"fields": "temp", "chart_type": "bar", "color": "#abcdef"})
    payload = out.split(CHART_OPEN, 1)[1].split(CHART_CLOSE, 1)[0]
    spec = json.loads(payload)
    assert spec["vega_lite"]["mark"]["type"] == "bar"
    assert spec["vega_lite"]["encoding"]["color"] == {"value": "#abcdef"}


def test_builder_drops_unknown_fields():
    cfg = make_config()
    spec = build_timeseries_chart_spec(_store(), cfg, ["temp", "bogus"])
    assert spec.fields == ["temp"]


def test_builder_clamps_window():
    cfg = make_config()
    spec = build_timeseries_chart_spec(_store(), cfg, ["temp"], window_minutes=10**9)
    assert spec.window_minutes <= 7 * 24 * 60


def test_chart_tool_wraps_spec_in_markers():
    cfg = make_config()
    tool = make_chart_tool(_store(), cfg)
    out = tool.invoke({"fields": "temp", "window_minutes": 30})
    assert CHART_OPEN in out and CHART_CLOSE in out
    payload = out.split(CHART_OPEN, 1)[1].split(CHART_CLOSE, 1)[0]
    spec = json.loads(payload)
    assert spec["fields"] == ["temp"]


def test_chart_tool_reports_no_valid_fields():
    cfg = make_config()
    tool = make_chart_tool(_store(), cfg)
    out = tool.invoke({"fields": "nonsense"})
    assert CHART_OPEN not in out
    assert "No valid fields" in out


def test_parse_chart_query_matches_field_labels():
    cfg = make_config()
    assert parse_chart_query(cfg, "show me the temp over time") == ["temp"]
    # Falls back to all fields when nothing matches.
    assert set(parse_chart_query(cfg, "how are things")) == {"temp", "moisture"}


def _client():
    cfg = make_config()
    registry, _, _ = make_registry(ts=_store())
    app = FastAPI()
    mount_visualization(app, cfg, registry, serve_dashboard=False)
    return TestClient(app)


def test_chart_endpoint_explicit_fields():
    body = _client().post("/api/viz/chart", json={"fields": ["temp"]}).json()
    assert body["source"] == "inline"
    assert body["vega_lite"]["data"]["values"]


def test_chart_endpoint_natural_language_query():
    body = _client().post("/api/viz/chart", json={"query": "plot the temp"}).json()
    assert body["fields"] == ["temp"]
