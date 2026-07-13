"""LangChain tool factories wired to the twin's live data stores."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.data.storage.base import DocumentStore, TimeSeriesStore
    from dyon.intelligent.knowledge_graph import KnowledgeGraph
    from dyon.services.ditto.client import DittoClient

log = logging.getLogger(__name__)

# Upper bound on any single tool result handed back to the LLM, so a hostile or
# runaway payload cannot blow the context window.
_MAX_TOOL_CHARS = 4000


def _as_data(value) -> str:
    """Fence untrusted tool output as ``<data>…</data>`` and cap its length.

    Sensor readings, Ditto state, and event payloads are all data an external
    system can influence, so they are marked as untrusted (the agent's system
    prompt tells the model never to follow instructions inside the fence) with
    the delimiters stripped from the value first so it can't close the fence.
    """
    text = str(value).replace("<data>", "").replace("</data>", "")
    if len(text) > _MAX_TOOL_CHARS:
        text = text[:_MAX_TOOL_CHARS] + "…[truncated]"
    return f"<data>\n{text}\n</data>"


def make_twin_state_tool(ditto_client: DittoClient):
    from langchain_core.tools import tool

    @tool
    async def get_twin_state() -> str:
        """Get the full current state of the digital twin from Eclipse Ditto."""
        try:
            thing = await ditto_client.get_thing()
            return _as_data(thing)
        except Exception as e:
            return f"Error fetching twin state: {e}"

    return get_twin_state


def make_sensor_tool(ts_store: TimeSeriesStore, config: TwinConfig):
    from langchain_core.tools import tool

    @tool
    def get_sensor_readings() -> str:
        """Get the latest sensor readings for all fields."""
        readings = {}
        for fname in config.field_names:
            readings[fname] = ts_store.get_latest(fname)
        return _as_data(readings)

    return get_sensor_readings


def make_diagnose_tool(kg: KnowledgeGraph, ts_store: TimeSeriesStore, config: TwinConfig):
    from langchain_core.tools import tool

    @tool
    def diagnose_asset() -> str:
        """Diagnose the asset by checking sensor readings against the knowledge graph."""
        readings = {
            f: v
            for f in config.field_names
            if (v := ts_store.get_latest(f)) is not None
        }
        symptoms = kg.diagnose_from_readings(readings)
        if not symptoms:
            return "No symptoms detected. Asset appears healthy."
        diagnoses = kg.diagnose(symptoms)
        if not diagnoses:
            return f"Symptoms detected: {symptoms}. No matching failure modes in knowledge graph."
        result = f"Active symptoms: {symptoms}\n\nDiagnoses:\n"
        for d in diagnoses:
            result += f"- {d['failure']} (severity: {d['severity']}): actions = {d['actions']}\n"
        return result

    return diagnose_asset


def make_event_log_tool(doc_store: DocumentStore):
    from langchain_core.tools import tool

    @tool
    def get_recent_events(n: int = 10) -> str:
        """Get the most recent events logged by the digital twin."""
        events = doc_store.get_recent_events(n)
        if not events:
            return "No recent events."
        lines = []
        for e in events:
            lines.append(
                f"[{e.get('severity', 'info')}] {e.get('event_type')} "
                f"at {e.get('timestamp')}: {e.get('payload')}"
            )
        return _as_data("\n".join(lines))

    return get_recent_events


def make_components_tool(kg: KnowledgeGraph):
    from langchain_core.tools import tool

    @tool
    def get_asset_components() -> str:
        """List the components of the asset as defined in the knowledge graph."""
        components = kg.get_components()
        return f"Components: {components}" if components else "No components registered."

    return get_asset_components
