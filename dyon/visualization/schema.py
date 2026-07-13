"""The visualization contract: Pydantic models the dashboard client renders.

The server emits a :class:`DashboardSpec` as JSON; the client renders it; the
agent emits :class:`ChartSpec` objects that slot into the same renderer. This
module is the single source of truth for what a dashboard *is* — everything
else (derivation, the API, the client) is in service of producing or consuming
these models.

Threshold direction is expressed as ``"above"`` / ``"below"`` here — the
human-facing sense of "alarm when the value goes above/below the threshold".
The core config (:class:`~dyon.core.config.SensorFieldSpec`) uses the equivalent
``"high"`` / ``"low"`` vocabulary; :mod:`dyon.visualization.derive` translates.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["above", "below"]

# How a dashboard's twin is composed. ``single`` is a lone asset twin; the rest
# mirror the collection types in :mod:`dyon.collection` (aggregate / collection /
# composite / network). The client renders a type-specific overview for each and
# federates the member twins' own dashboards beneath it.
Combination = Literal["single", "aggregate", "collection", "composite", "network"]

# The bootstrap dashboard's palette, matching the two reference dashboards so a
# derived dashboard looks like the ones already shipped. Exposed as a default so
# authors can override individual variables without restating the whole theme.
DEFAULT_THEME: dict[str, str] = {
    "--bg": "#0f1117",
    "--panel": "#171a21",
    "--text": "#e6e8ee",
    "--muted": "#8b90a0",
    "--accent": "#4f8ef7",
    "--ok": "#39d98a",
    "--warn": "#f7b84f",
    "--crit": "#f7564f",
}


class FieldBinding(BaseModel):
    """Binds a panel element to a single sensor field and its alarm bounds."""

    field: str                          # matches SensorFieldSpec.name
    label: str | None = None            # display label; defaults to field
    unit: str | None = None             # defaults from SensorFieldSpec.unit
    warn: float | None = None           # from warn_threshold
    crit: float | None = None           # from crit_threshold
    direction: Direction = "above"      # from threshold_direction


class AlarmRule(BaseModel):
    """A single threshold that, when crossed, raises a warn/crit alarm."""

    field: str
    level: Literal["warn", "crit"]
    threshold: float
    direction: Direction
    message: str | None = None


class KpiSpec(BaseModel):
    """A single-value tile with an optional inline trend sparkline."""

    id: str
    binding: FieldBinding
    format: str = "{:.1f}"
    sparkline: bool = True


class ChartSpec(BaseModel):
    """A renderable chart.

    Built-in panels populate ``vega_lite`` on the client from ``fields`` and
    ``window_minutes``. The agent may instead emit a complete ``vega_lite``
    object directly — the "on-the-fly chart" path — and it renders through the
    very same component.
    """

    id: str
    title: str | None = None
    kind: Literal["timeseries", "bar", "scatter", "custom"] = "timeseries"
    fields: list[str] = Field(default_factory=list)
    window_minutes: int = 120
    vega_lite: dict | None = None       # full Vega-Lite spec (agent path / overrides)
    source: Literal["history", "forecast", "inline"] = "history"


class FsmSpec(BaseModel):
    """A finite-state diagram that highlights the current state."""

    id: str
    title: str
    states: list[str]
    state_field: str                    # which value drives the highlight
    edges: list[tuple[str, str]] = Field(default_factory=list)


class SceneSpec(BaseModel):
    """An optional 3D asset view. Ignored entirely when 3D is unavailable.

    Beyond the static model, the scene can reflect the asset's *condition* live:
    ``stress_field`` (with its warn/crit bounds and direction) drives a colour
    wash over the model — healthy → amber → brown — and, if ``stage_models`` maps
    a level (``ok``/``warn``/``crit``) to its own GLB, the model is swapped for
    that stage. This is the framework's honest middle ground: free assets are not
    rigged for true mesh deformation, so condition shows as tint + stage swap +
    hotspot colour rather than geometry change."""

    id: str
    model_url: str | None = None        # GLB/GLTF; None => 2D schematic fallback
    bindings: list[FieldBinding] = Field(default_factory=list)
    hotspots: list[dict] = Field(default_factory=list)   # {field, position, label}
    fallback_svg: str | None = None     # 2D schematic when WebGL/compute absent
    poster: str | None = None

    # Live condition cues
    stress_field: str | None = None     # field that drives tint / stage swap
    stress_warn: float | None = None
    stress_crit: float | None = None
    stress_direction: Direction = "above"
    stage_models: dict[str, str] = Field(default_factory=dict)   # level -> GLB url


class MemberRef(BaseModel):
    """A constituent twin of a combined dashboard.

    ``api_base`` is the URL the *browser* uses to reach that member's own twin
    API (e.g. ``http://host:8502``); the client federates the member's own
    dashboard — its spec, live stream, and 3D scene — from there. It must be
    reachable from where the dashboard is opened; CORS on a member twin is open
    in dev mode and restricted to configured origins in production mode."""

    id: str
    name: str
    asset_type: str = "generic_asset"
    api_base: str
    parent: str | None = None           # immediate parent id, for hierarchy views


class TopologyEdge(BaseModel):
    """A directed edge between two members.

    Carries both a composite's boundary-condition flows and a network's typed
    relationships; ``kind`` distinguishes them and ``label`` annotates the edge
    (e.g. the relationship type or the field a boundary condition transfers)."""

    source: str                         # member id
    target: str                         # member id
    kind: Literal["flow", "relationship"] = "flow"
    label: str | None = None


class PanelSpec(BaseModel):
    """One panel on the dashboard grid. ``config`` carries the kind-specific
    payload — typically one of the ``*Spec`` models above, serialised."""

    id: str
    kind: Literal["kpi", "chart", "fsm", "events", "alarms",
                  "scene", "chat", "html"]
    title: str | None = None
    span: int = 1                       # grid columns (1..4)
    config: dict = Field(default_factory=dict)


class DashboardSpec(BaseModel):
    """The whole dashboard: theme, layout, and the panels to render."""

    asset_id: str
    asset_name: str
    asset_type: str
    theme: dict = Field(default_factory=lambda: dict(DEFAULT_THEME))
    columns: int = 4
    panels: list[PanelSpec] = Field(default_factory=list)
    alarms: list[AlarmRule] = Field(default_factory=list)
    chat_enabled: bool = True
    voice_enabled: bool = False
    scene_enabled: bool = False
    api_base: str = ""                  # client overridable; "" => same origin

    # Combination: ``single`` (default) is a lone twin and uses ``panels`` as
    # before. Anything else is a combined twin — the client renders a
    # type-specific overview from ``members`` + ``hierarchy``/``edges`` and
    # federates each member's own dashboard from its ``api_base``.
    combination: Combination = "single"
    members: list[MemberRef] = Field(default_factory=list)
    hierarchy: dict[str, list[str]] = Field(default_factory=dict)   # parent id -> child ids
    edges: list[TopologyEdge] = Field(default_factory=list)         # flows / relationships
