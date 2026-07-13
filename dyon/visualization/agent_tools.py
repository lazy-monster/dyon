"""Agent-facing chart/forecast tools and the shared Vega-Lite builder.

``make_chart_tool`` / ``make_forecast_tool`` are LangChain tool factories in the
same shape as :mod:`dyon.intelligent.tools`: a twin opts in by adding them in its
agent's ``_build_extra_tools()`` — no change to ``DiagnosticAgent`` itself. When
the agent visualises history ("show the last three days of field X"), the tool
queries the store, builds a Vega-Lite :class:`ChartSpec`, and returns it wrapped
in a marker the dashboard's chat renderer detects and renders inline.

``build_timeseries_chart_spec`` is the pure builder shared with the direct
``POST /api/viz/chart`` endpoint, so the agent path and the non-agent path
produce identical charts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from dyon.visualization.schema import ChartSpec

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.data.storage.base import TimeSeriesStore

# Sentinel the chat renderer scans for in the agent's streamed output. Kept
# verbose and unlikely to collide with ordinary prose.
CHART_OPEN = "<<<DYON_CHART>>>"
CHART_CLOSE = "<<<END_CHART>>>"

_MAX_WINDOW = 7 * 24 * 60   # one week, in minutes

# Chart shapes the agent may pick. Each maps a friendly name to a Vega-Lite mark.
# Aliases ("scatter" -> point) keep the tool forgiving about the model's wording.
_CHART_TYPES = {"line", "area", "bar", "point", "scatter"}
_DEFAULT_COLOR = "#4f8ef7"


def _build_mark(chart_type: str, show_points: bool, interpolate: str) -> dict:
    """Translate a friendly chart type into a Vega-Lite mark definition."""
    ct = (chart_type or "line").strip().lower()
    if ct == "scatter":
        ct = "point"
    if ct not in _CHART_TYPES:
        ct = "line"
    if ct == "line":
        return {"type": "line", "interpolate": interpolate, "point": bool(show_points)}
    if ct == "area":
        return {"type": "area", "interpolate": interpolate, "opacity": 0.7, "line": True}
    if ct == "bar":
        return {"type": "bar"}
    return {"type": "point", "filled": True, "size": 55}  # point / scatter


def _normalize_rows(rows: list[dict]) -> None:
    """Min-max scale each field's values into 0..1, in place, so series with very
    different units can be compared on one axis."""
    by_field: dict[str, list[dict]] = {}
    for r in rows:
        if isinstance(r.get("v"), int | float):
            by_field.setdefault(r["field"], []).append(r)
    for points in by_field.values():
        vals = [p["v"] for p in points]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        for p in points:
            p["v"] = (p["v"] - lo) / span


def _iso(ts) -> str | None:
    """Normalise a timestamp to ISO-8601 for a Vega-Lite ``temporal`` axis.

    The store yields Unix *seconds* (a bare float); Vega-Lite reads a bare number
    on a temporal axis as epoch *milliseconds*, collapsing every point onto ~1970
    — one column, one smear. Emitting ISO strings makes the axis correct."""
    if ts is None:
        return None
    if isinstance(ts, int | float):
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    return str(ts)


def _rows(history: dict[str, list[dict]]) -> list[dict]:
    rows: list[dict] = []
    for field, points in history.items():
        for p in points:
            rows.append({
                "t": _iso(p.get("ts", p.get("t"))),
                "v": p.get("value", p.get("v")),
                "field": field,
            })
    return rows


def build_timeseries_chart_spec(
    ts_store: TimeSeriesStore,
    config: TwinConfig,
    fields: list[str],
    window_minutes: int = 120,
    title: str | None = None,
    chart_type: str = "line",
    color: str | None = None,
    show_points: bool = False,
    interpolate: str = "monotone",
    normalize: bool = False,
    y_title: str | None = None,
) -> ChartSpec:
    """Query recent history for ``fields`` and return a ready-to-render
    :class:`ChartSpec` carrying a complete inline Vega-Lite spec.

    The chart is customisable so the agent can pick the most expressive form:

    - ``chart_type`` — ``line`` (default), ``area``, ``bar``, ``point``/``scatter``.
    - ``color`` — a hex colour for a single series (ignored when several fields are
      plotted, where each gets its own colour automatically).
    - ``show_points`` — mark each sample on a line.
    - ``interpolate`` — line/area smoothing (``monotone``, ``linear``, ``step``…).
    - ``normalize`` — min-max scale each field to 0..1 so series with different
      units share one axis.
    - ``title`` / ``y_title`` — chart and y-axis labels.
    """
    window_minutes = max(1, min(window_minutes, _MAX_WINDOW))
    valid = [f for f in fields if f in config.field_names]
    history = ts_store.query_recent_fields(valid, minutes=window_minutes) if valid else {}
    rows = _rows(history)
    if normalize:
        _normalize_rows(rows)
    multi = len({r["field"] for r in rows}) > 1
    if multi:
        color_enc: dict = {"field": "field", "type": "nominal"}
    else:
        color_enc = {"value": color or _DEFAULT_COLOR}
    y_label = y_title if y_title is not None else ("normalised (0–1)" if normalize else None)
    vega = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": title or ("Trend — " + ", ".join(valid) if valid else "Trend"),
        "data": {"values": rows},
        "mark": _build_mark(chart_type, show_points, interpolate),
        "width": "container",
        "height": 240,
        "encoding": {
            "x": {"field": "t", "type": "temporal", "title": None},
            "y": {"field": "v", "type": "quantitative", "title": y_label},
            "color": color_enc,
        },
    }
    return ChartSpec(
        id="agent-chart",
        title=title or "Trend",
        kind="timeseries",
        fields=valid,
        window_minutes=window_minutes,
        vega_lite=vega,
        source="inline",
    )


def _wrap(spec: ChartSpec) -> str:
    return CHART_OPEN + spec.model_dump_json() + CHART_CLOSE


def make_chart_tool(ts_store: TimeSeriesStore, config: TwinConfig):
    from langchain_core.tools import tool

    field_list = ", ".join(config.field_names) or "(none configured)"

    @tool
    def make_chart(
        fields: str,
        window_minutes: int = 120,
        chart_type: str = "line",
        color: str | None = None,
        show_points: bool = False,
        normalize: bool = False,
        title: str | None = None,
    ) -> str:
        """Plot recent sensor history as a chart that is shown to the user.

        This is the ONLY way to show the user a chart. Call it for any request to
        see, show, plot, graph, chart, visualise, draw, or display sensor data, a
        trend, or history over time — e.g. "show me X", "plot the last hour of Y",
        "chart the temperature trend", "what does X look like over time". The
        chart renders inline for the user automatically, so do NOT answer such a
        request by describing the numbers in prose; call this tool instead and add
        only a brief sentence of context.

        Pick the form that tells the story best, and honour styling the user asks
        for ("as a bar chart", "in green", "with the points marked").

        Args:
            fields: comma-separated sensor field name(s) to plot, drawn from the
                available field names below. Plot several to compare them.
            window_minutes: how far back to look, in minutes (default 120).
            chart_type: "line" (default), "area", "bar", or "point"/"scatter".
            color: optional hex colour for a single series, e.g. "#22c55e".
            show_points: mark each individual sample on a line.
            normalize: scale each field to 0..1 so fields with different units
                (e.g. a temperature and a pressure) can be compared on one axis.
            title: optional chart title.
        """
        names = [f.strip() for f in fields.split(",") if f.strip()]
        spec = build_timeseries_chart_spec(
            ts_store, config, names, window_minutes,
            title=title, chart_type=chart_type, color=color,
            show_points=show_points, normalize=normalize,
        )
        if not spec.fields:
            return f"No valid fields to chart. Available fields: {field_list}."
        return (
            f"Displaying a {(chart_type or 'line').lower()} chart of "
            f"{', '.join(spec.fields)} over the last {spec.window_minutes} "
            f"minutes.\n{_wrap(spec)}"
        )

    # Expose the valid field names in the tool description so the model passes
    # correct arguments and recognises when a request maps to a real field.
    make_chart.description += f"\n\nAvailable field names: {field_list}."
    return make_chart


def make_forecast_tool(ts_store: TimeSeriesStore, config: TwinConfig):
    from langchain_core.tools import tool

    @tool
    def forecast_field(field: str, steps: int = 24) -> str:
        """Forecast one sensor field into the future and chart it for the user.

        Call this for any request to forecast, predict, project, or extrapolate a
        sensor's future values — e.g. "forecast X", "what will Y be tomorrow",
        "project the temperature for the next day". The forecast chart renders
        inline automatically, so do NOT describe the projection in prose instead;
        call this tool.

        Args:
            field: a single sensor field name to forecast.
            steps: how many hourly steps to project (default 24).

        Requires the forecasting backend to be installed.
        """
        if field not in config.field_names:
            return f"Unknown field '{field}'. Available: {', '.join(config.field_names)}."
        try:
            from dyon.simulation.forecaster import ProphetForecaster
        except Exception:
            return "Forecasting backend not installed (pip install 'dyon[forecast]')."
        forecaster = ProphetForecaster(ts_store, field)
        forecaster.fit()
        series = forecaster.predict(periods=max(1, steps), freq="h")
        if not series:
            return f"Not enough history to forecast '{field}'."
        rows = [
            {
                "t": r["ds"].isoformat() if hasattr(r["ds"], "isoformat") else r["ds"],
                "v": r["yhat"],
                "field": f"{field} (forecast)",
            }
            for r in series
        ]
        spec = ChartSpec(
            id="agent-forecast",
            title=f"{field} forecast",
            kind="timeseries",
            fields=[field],
            vega_lite={
                "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                "title": f"{field} — {steps}-step forecast",
                "data": {"values": rows},
                "mark": {"type": "line", "interpolate": "monotone"},
                "width": "container",
                "height": 240,
                "encoding": {
                    "x": {"field": "t", "type": "temporal", "title": None},
                    "y": {"field": "v", "type": "quantitative", "title": None},
                    "color": {"value": "#f7b84f"},
                },
            },
            source="inline",
        )
        return f"Forecast for {field}:\n{_wrap(spec)}"

    return forecast_field


def parse_chart_query(config: TwinConfig, query: str) -> list[str]:
    """Best-effort: pick the sensor fields mentioned in a natural-language
    request. Used by the direct endpoint when no explicit field list is given."""
    q = query.lower()
    hits = [f for f in config.field_names if f.lower() in q]
    # Also match on de-underscored labels (e.g. "flow rate" -> flow_rate).
    for f in config.field_names:
        if f.replace("_", " ").lower() in q and f not in hits:
            hits.append(f)
    return hits or list(config.field_names)


__all__ = [
    "CHART_CLOSE",
    "CHART_OPEN",
    "build_timeseries_chart_spec",
    "make_chart_tool",
    "make_forecast_tool",
    "parse_chart_query",
]
