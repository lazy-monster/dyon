"""Helpers for authoring a 3D :class:`SceneSpec`.

The 3D viewport renders client-side via ``<model-viewer>``; these helpers only
build the spec the client consumes — placing a hotspot for each bound sensor
field so the overlay can show the live value and turn warn/crit colour. The
whole panel is optional and capability-gated in the browser: with no WebGL2 the
client falls back to ``SceneSpec.fallback_svg``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dyon.visualization.schema import FieldBinding, SceneSpec

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig


def binding_to_hotspot(binding: FieldBinding, position: str) -> dict:
    """Map a :class:`FieldBinding` plus a ``<model-viewer>`` position string to a
    hotspot dict the client can render and colour from the live value."""
    return {
        "field": binding.field,
        "label": binding.label or binding.field,
        "unit": binding.unit,
        "warn": binding.warn,
        "crit": binding.crit,
        "direction": binding.direction,
        "position": position,           # e.g. "0m 1m 0m" in model space
    }


def build_scene_spec(
    *,
    id: str = "scene",
    model_url: str | None = None,
    bindings: list[FieldBinding] | None = None,
    positions: dict[str, str] | None = None,
    fallback_svg: str | None = None,
    poster: str | None = None,
    stress_field: str | None = None,
    stress_warn: float | None = None,
    stress_crit: float | None = None,
    stress_direction: str = "above",
    stage_models: dict[str, str] | None = None,
) -> SceneSpec:
    """Assemble a :class:`SceneSpec`.

    ``positions`` maps a field name to its hotspot position in model space; any
    bound field without a position is still carried (the client can place it
    using a default layout) so authoring a scene is incremental. The optional
    ``stress_*`` bounds and ``stage_models`` drive the live condition cues (tint
    and stage swap) described on :class:`SceneSpec`.
    """
    bindings = bindings or []
    positions = positions or {}
    hotspots = [
        binding_to_hotspot(b, positions.get(b.field, ""))
        for b in bindings
    ]
    return SceneSpec(
        id=id,
        model_url=model_url,
        bindings=bindings,
        hotspots=hotspots,
        fallback_svg=fallback_svg,
        poster=poster,
        stress_field=stress_field,
        stress_warn=stress_warn,
        stress_crit=stress_crit,
        stress_direction=stress_direction,  # type: ignore[arg-type]
        stage_models=stage_models or {},
    )


def scene_from_config(
    config: TwinConfig,
    *,
    model_url: str | None = None,
    positions: dict[str, str] | None = None,
    fallback_svg: str | None = None,
    stress_field: str | None = None,
    stage_models: dict[str, str] | None = None,
) -> SceneSpec:
    """Build a scene whose hotspots are derived from the twin's sensor fields,
    inheriting each field's unit and thresholds.

    Pass ``stress_field`` to drive the live tint/stage cues from one of those
    fields; its warn/crit bounds and direction are taken from the config so the
    model colours on the same logic as that field's KPI tile.
    """
    bindings = [
        FieldBinding(
            field=f.name,
            label=f.name.replace("_", " ").title(),
            unit=f.unit or None,
            warn=f.warn_threshold,
            crit=f.crit_threshold,
            direction="below" if f.threshold_direction == "low" else "above",
        )
        for f in config.sensor_fields
    ]
    stress = next((f for f in config.sensor_fields if f.name == stress_field), None)
    return build_scene_spec(
        model_url=model_url,
        bindings=bindings,
        positions=positions,
        fallback_svg=fallback_svg,
        stress_field=stress_field,
        stress_warn=stress.warn_threshold if stress else None,
        stress_crit=stress.crit_threshold if stress else None,
        stress_direction=("below" if stress and stress.threshold_direction == "low" else "above"),
        stage_models=stage_models,
    )


__all__ = ["binding_to_hotspot", "build_scene_spec", "scene_from_config"]
