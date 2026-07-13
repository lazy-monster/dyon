"""``GET /api/viz/agents`` — a live snapshot of the multi-agent system.

The dashboard's "Agents" tab shows what the twin's reasoning layer is doing: for
each agent, its latest observe→reason→act cycle and the tool calls it made. This
reads the snapshot the :class:`~dyon.intelligent.mas.MultiAgentSystem` already
keeps per cycle (``get_agent_detail``), so it adds no load to the agents
themselves and needs no new persistence — the client just polls it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from dyon.visualization.context import VizContext


def build_agents_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.get("/agents")
    async def viz_agents():
        mas = ctx.mas
        if mas is None:
            return {"available": False, "agents": []}

        get_detail = getattr(mas, "get_agent_detail", None)
        agents = []
        for agent in getattr(mas, "agents", []):
            name = getattr(agent, "agent_name", "agent")
            detail = (get_detail(name) or {}) if callable(get_detail) else {}
            findings = detail.get("findings", {}) or {}
            observations = detail.get("observations", {}) or {}
            agents.append({
                "agent_name": name,
                "domain": getattr(agent, "domain", detail.get("domain", "")),
                "priority": getattr(agent, "priority", 0),
                "anomaly": bool(observations.get("anomaly_detected", False)),
                "severity": findings.get("severity", "info"),
                "action": findings.get("action", "monitoring"),
                "summary": findings.get("summary", ""),
                "observations": observations,
                "tool_calls": detail.get("tool_calls", []),
                "error": detail.get("error"),
                "ts_s": detail.get("ts_s"),
            })

        return {
            "available": True,
            "monitor_interval": getattr(mas, "interval", None),
            "agents": agents,
        }

    return router
