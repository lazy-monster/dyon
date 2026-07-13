"""Zero-config bootstrap: turn a ``TwinConfig`` into a ``DashboardSpec``.

``derive_default_spec`` is a pure function (no I/O) so it is fully unit-testable
and cheap to call on every request. It reads only ``config.sensor_fields`` plus
the asset identity, and produces KPI tiles, a grouped time-series chart, alarm
rules, and the events/alarms/chat panels — i.e. a usable dashboard with no
authoring effort. Authors customise by mutating the returned spec or building
their own from scratch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dyon.visualization.schema import (
    AlarmRule,
    ChartSpec,
    Combination,
    DashboardSpec,
    Direction,
    FieldBinding,
    KpiSpec,
    MemberRef,
    PanelSpec,
    TopologyEdge,
)

if TYPE_CHECKING:
    from dyon.core.config import SensorFieldSpec, TwinConfig


def _direction(field: SensorFieldSpec) -> Direction:
    """Translate the config's ``high``/``low`` vocabulary to the contract's
    ``above``/``below``."""
    return "below" if field.threshold_direction == "low" else "above"


def _binding(field: SensorFieldSpec) -> FieldBinding:
    return FieldBinding(
        field=field.name,
        label=field.name.replace("_", " ").title(),
        unit=field.unit or None,
        warn=field.warn_threshold,
        crit=field.crit_threshold,
        direction=_direction(field),
    )


def _alarm_rules(field: SensorFieldSpec) -> list[AlarmRule]:
    rules: list[AlarmRule] = []
    direction = _direction(field)
    if field.warn_threshold is not None:
        rules.append(AlarmRule(
            field=field.name, level="warn",
            threshold=field.warn_threshold, direction=direction,
        ))
    if field.crit_threshold is not None:
        rules.append(AlarmRule(
            field=field.name, level="crit",
            threshold=field.crit_threshold, direction=direction,
        ))
    return rules


def derive_default_spec(config: TwinConfig) -> DashboardSpec:
    """Build a ready-to-render :class:`DashboardSpec` from a twin's config."""
    fields = list(config.sensor_fields)
    columns = 4

    panels: list[PanelSpec] = []
    alarms: list[AlarmRule] = []

    # One KPI tile per field, in declaration order.
    for field in fields:
        binding = _binding(field)
        panels.append(PanelSpec(
            id=f"kpi-{field.name}",
            kind="kpi",
            title=binding.label,
            span=1,
            config=KpiSpec(id=f"kpi-{field.name}", binding=binding).model_dump(),
        ))
        alarms.extend(_alarm_rules(field))

    # Group fields by unit so same-unit series share an axis. Plottable fields
    # are those with a numeric nominal (derived/computed fields have nominal None
    # but may still stream, so we include any field here — the chart just shows
    # whatever history exists).
    by_unit: dict[str, list[str]] = {}
    for field in fields:
        by_unit.setdefault(field.unit or "", []).append(field.name)

    for idx, (unit, names) in enumerate(by_unit.items()):
        title = f"Trend — {unit}" if unit else "Trend"
        panels.append(PanelSpec(
            id=f"chart-{idx}",
            kind="chart",
            title=title,
            span=columns if len(by_unit) == 1 else 2,
            config=ChartSpec(
                id=f"chart-{idx}",
                title=title,
                kind="timeseries",
                fields=names,
            ).model_dump(),
        ))

    # Alarm banner (driven by the rules above) and a scrolling event log.
    panels.append(PanelSpec(
        id="alarms", kind="alarms", title="Alarms",
        span=columns,
        config={"rules": [r.model_dump() for r in alarms]},
    ))
    panels.append(PanelSpec(
        id="events", kind="events", title="Event Log", span=2,
    ))

    # The conversational interface is on by default — the agent path exists.
    panels.append(PanelSpec(
        id="chat", kind="chat", title="Ask the Twin", span=2,
    ))

    return DashboardSpec(
        asset_id=config.asset_id,
        asset_name=config.asset_name,
        asset_type=config.asset_type,
        columns=columns,
        panels=panels,
        alarms=alarms,
        chat_enabled=True,
        voice_enabled=False,
        scene_enabled=False,
    )


def _prettify(twin_id: str) -> str:
    """A human label for a twin id when no explicit name is given."""
    return twin_id.replace("_", " ").replace("-", " ").strip().title() or twin_id


def derive_combined_spec(
    *,
    combination: Combination,
    asset_id: str,
    asset_name: str,
    members: list[MemberRef],
    hierarchy: dict[str, list[str]] | None = None,
    edges: list[TopologyEdge] | None = None,
    asset_type: str | None = None,
    theme: dict | None = None,
    chat_enabled: bool = False,
    voice_enabled: bool = False,
) -> DashboardSpec:
    """Build a federated :class:`DashboardSpec` for a combined twin.

    The overview is rendered client-side from ``combination`` + ``members`` and
    the ``hierarchy``/``edges`` topology; each member's own panels (KPIs, charts,
    3D scene) are pulled live from its ``api_base``. ``panels`` is therefore left
    empty — there is nothing to render server-side here. Pass ``combination
    ="single"`` only if you want the lone-twin layout with no members.
    """
    spec = DashboardSpec(
        asset_id=asset_id,
        asset_name=asset_name,
        asset_type=asset_type or f"{combination} twin",
        combination=combination,
        members=list(members),
        hierarchy=dict(hierarchy or {}),
        edges=list(edges or []),
        panels=[],
        alarms=[],
        chat_enabled=chat_enabled,
        voice_enabled=voice_enabled,
    )
    if theme:
        spec.theme.update(theme)
    return spec


def combined_spec_from_twin(
    twin,
    member_api_bases: dict[str, str],
    *,
    asset_name: str | None = None,
    member_names: dict[str, str] | None = None,
    member_types: dict[str, str] | None = None,
    **kwargs,
) -> DashboardSpec:
    """Bridge a runtime collection twin to a federated :class:`DashboardSpec`.

    Reads ``collection_type`` and ``component_ids`` from any
    :class:`~dyon.collection.base.AbstractCollectionTwin`, plus the
    type-specific topology where present — a :class:`CompositeDT`'s ``hierarchy``
    and boundary conditions (rendered as ``flow`` edges) and a :class:`NetworkDT`'s
    typed ``relationships`` (rendered as ``relationship`` edges).

    ``member_api_bases`` maps each component id to the URL the browser uses to
    reach that member's twin API; it is required because a collection twin only
    knows component ids, not where their dashboards are served. ``member_names``
    /``member_types`` override the auto-derived label/type per member.
    """
    member_names = member_names or {}
    member_types = member_types or {}
    ids = list(getattr(twin, "component_ids", []))
    hierarchy = dict(getattr(twin, "hierarchy", {}) or {})

    parent_of: dict[str, str] = {}
    for parent, children in hierarchy.items():
        for child in children:
            parent_of[child] = parent

    members = [
        MemberRef(
            id=tid,
            name=member_names.get(tid, _prettify(tid)),
            asset_type=member_types.get(tid, "generic_asset"),
            api_base=member_api_bases.get(tid, ""),
            parent=parent_of.get(tid),
        )
        for tid in ids
    ]

    edges: list[TopologyEdge] = []
    for bc in getattr(twin, "boundaries", []) or []:
        edges.append(TopologyEdge(
            source=bc.source_twin, target=bc.target_twin, kind="flow",
            label=getattr(bc, "target_field", None) or getattr(bc, "source_field", None),
        ))
    for rel in getattr(twin, "relationships", []) or []:
        edges.append(TopologyEdge(
            source=rel.source_twin, target=rel.target_twin, kind="relationship",
            label=getattr(rel, "relationship_type", None),
        ))

    asset_id = getattr(twin, "collection_id", "collection")
    return derive_combined_spec(
        combination=getattr(twin, "collection_type", "aggregate"),
        asset_id=asset_id,
        asset_name=asset_name or _prettify(asset_id),
        members=members,
        hierarchy=hierarchy,
        edges=edges,
        **kwargs,
    )
