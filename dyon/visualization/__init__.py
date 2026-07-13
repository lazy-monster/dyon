"""Dyon visualization: config-driven dashboards, live streaming, an agentic
conversational interface, and an optional 3D asset viewport.

Everything here is additive and opt-in. A twin that never imports this package
or calls :func:`mount_visualization` behaves exactly as it did before.

The data contract (:mod:`schema`) and the zero-config bootstrap
(:func:`derive_default_spec`) carry no heavy dependencies and are always
importable. The runtime wiring (``mount_visualization``, ``create_dashboard_app``)
pulls in FastAPI, so it is imported lazily on first access and never at package
import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dyon.visualization.agent_tools import (
    build_timeseries_chart_spec,
    make_chart_tool,
    make_forecast_tool,
)
from dyon.visualization.derive import (
    combined_spec_from_twin,
    derive_combined_spec,
    derive_default_spec,
)
from dyon.visualization.scene import (
    build_scene_spec,
    scene_from_config,
)
from dyon.visualization.schema import (
    AlarmRule,
    ChartSpec,
    DashboardSpec,
    FieldBinding,
    FsmSpec,
    KpiSpec,
    MemberRef,
    PanelSpec,
    SceneSpec,
    TopologyEdge,
)

if TYPE_CHECKING:
    from dyon.visualization.chat_agent import (
        DashboardChatAgent,
        make_dashboard_chat_agent,
    )
    from dyon.visualization.serve import (
        create_combined_dashboard_app,
        create_dashboard_app,
        mount_visualization,
    )

__all__ = [
    "AlarmRule",
    "ChartSpec",
    "DashboardChatAgent",
    "DashboardSpec",
    "FieldBinding",
    "FsmSpec",
    "KpiSpec",
    "MemberRef",
    "PanelSpec",
    "SceneSpec",
    "TopologyEdge",
    "build_scene_spec",
    "build_timeseries_chart_spec",
    "combined_spec_from_twin",
    "create_combined_dashboard_app",
    "create_dashboard_app",
    "derive_combined_spec",
    "derive_default_spec",
    "make_chart_tool",
    "make_dashboard_chat_agent",
    "make_forecast_tool",
    "mount_visualization",
    "scene_from_config",
]


def __getattr__(name: str):
    # Lazy re-export so `from dyon.visualization import mount_visualization`
    # works without importing FastAPI/LangChain when only the schema/derive path
    # is used.
    if name in (
        "mount_visualization",
        "create_dashboard_app",
        "create_combined_dashboard_app",
    ):
        from dyon.visualization import serve

        return getattr(serve, name)
    if name in ("DashboardChatAgent", "make_dashboard_chat_agent"):
        from dyon.visualization import chat_agent

        return getattr(chat_agent, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
