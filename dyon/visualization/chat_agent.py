"""The dashboard's conversational agent.

The "Ask the Twin" panel needs one conversational agent. Rather than borrow
whichever agent happens to lead a twin's monitoring multi-agent system, the
framework ships :class:`DashboardChatAgent` for exactly this role: a
:class:`~dyon.intelligent.agent.DiagnosticAgent` tuned for dashboard Q&A and
equipped with the chart and forecast tools, so it can answer in prose *and* draw
inline charts when asked to visualise something.

A twin builds one with :func:`make_dashboard_chat_agent` and hands it to
:func:`~dyon.visualization.serve.mount_visualization` as ``chat_agent=``. The
slot is an override point: pass any object exposing an async ``ask(str)`` (and
optionally ``ask_stream(str)``) to replace it entirely.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from dyon.intelligent.agent import DiagnosticAgent, build_llm
from dyon.visualization.agent_tools import (
    CHART_CLOSE,
    CHART_OPEN,
    make_chart_tool,
    make_forecast_tool,
)

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.data.storage.base import DocumentStore, TimeSeriesStore
    from dyon.intelligent.knowledge_graph import KnowledgeGraph
    from dyon.services.ditto.client import DittoClient

# A chart marker is produced inside the chart/forecast tool's return value, so it
# lands in the agent's intermediate steps — not in the LLM's final prose. This
# matches the whole ``<<<DYON_CHART>>>…<<<END_CHART>>>`` block so it can be lifted
# out and forwarded to the dashboard renderer.
_MARKER_RE = re.compile(re.escape(CHART_OPEN) + r".*?" + re.escape(CHART_CLOSE), re.DOTALL)


class DashboardChatAgent(DiagnosticAgent):
    """A general-purpose chat agent for the dashboard.

    Inherits the diagnostic toolset (sensor reads, and — when their backing
    stores are supplied — twin state, knowledge-graph diagnosis, and the event
    log) and adds the chart/forecast tools so the panel can render visuals.
    """

    agent_name = "dashboard_chat"
    domain = "dashboard"
    priority = 100

    def _system_prompt(self) -> str:
        return (
            f"You are the assistant for the live dashboard of the digital twin "
            f"'{self.config.asset_name}' (type: {self.config.asset_type}, "
            f"ID: {self.config.asset_id}). You help a human operator understand "
            "the asset's current status, recent history, and likely near future. "
            "Use your tools to ground every answer in real data — never invent "
            "numbers. When the user asks to see, plot, visualise, chart, or trend "
            "something, call the make_chart tool (or forecast_field for a "
            "projection); the chart is rendered to them automatically, so do not "
            "describe a chart in words instead of calling the tool. Be concise, "
            "cite specific sensor values and units, and format replies in "
            "Markdown."
        )

    def _build_extra_tools(self) -> list:
        return [
            make_chart_tool(self._ts_store, self.config),
            make_forecast_tool(self._ts_store, self.config),
        ]

    async def ask(self, question: str) -> str:
        """Answer, then re-attach any chart the model drew via a tool.

        A tool-calling LLM puts the chart tool's marker in an intermediate step
        and returns only prose as its final answer, so the marker would never
        reach the dashboard. Lift any chart marker out of the tool outputs and
        append it (unless the model happened to echo it), so a chart renders
        whenever the model actually called ``make_chart``/``forecast_field``.
        """
        output = await super().ask(question)
        charts: list[str] = []
        for _action, observation in self._last_intermediate_steps:
            for marker in _MARKER_RE.findall(str(observation)):
                if marker not in output and marker not in charts:
                    charts.append(marker)
        if charts:
            output = output.rstrip() + "\n\n" + "\n".join(charts)
        return output


def make_dashboard_chat_agent(
    config: TwinConfig,
    *,
    ts_store: TimeSeriesStore,
    llm=None,
    doc_store: DocumentStore | None = None,
    ditto_client: DittoClient | None = None,
    knowledge_graph: KnowledgeGraph | None = None,
) -> DashboardChatAgent:
    """Build the dashboard chat agent.

    Only ``config`` and ``ts_store`` are required; supplying ``doc_store``,
    ``ditto_client``, and ``knowledge_graph`` enables the corresponding tools.
    ``llm`` defaults to one built from ``config.llm``.
    """
    return DashboardChatAgent(
        config,
        llm=llm or build_llm(config),
        ts_store=ts_store,
        doc_store=doc_store,
        ditto_client=ditto_client,
        knowledge_graph=knowledge_graph,
    )


__all__ = ["DashboardChatAgent", "make_dashboard_chat_agent"]
