"""MultiAgentSystem: coordinates multiple TwinAgent instances."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent
from dyon.intelligent.base import TwinAgent

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore

log = logging.getLogger(__name__)


def _safe_payload(v):
    """Recursively convert a value to a MongoDB-safe type."""
    if isinstance(v, dict):
        return {k: _safe_payload(val) for k, val in v.items()}
    if isinstance(v, list | tuple):
        return [_safe_payload(i) for i in v]
    if isinstance(v, str | int | float | bool | type(None)):
        return v
    return str(v)


class MultiAgentSystem(LayerBase):
    """
    Coordinates multiple TwinAgent instances.

    In the monitor loop each agent is called in priority order.
    If an agent detects an anomaly, reason() and act() are invoked.

    Pass ``cache`` to enable per-agent status caching in Redis.  After each
    agent iteration the status dict is stored under the key
    ``mas_agent_<agent_name>`` with fields:
        agent_name, domain, anomaly (bool), action, severity, detail, ts_s

    Pass ``doc_store`` to persist every agent decision to MongoDB so findings
    survive restarts and can be queried historically.  Both stores can be
    provided simultaneously: Redis for fast live reads, MongoDB for durability.
    """

    layer_name = "intelligent"
    service_name = "intelligent"
    dependencies: list[str] = []

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        agents: list[TwinAgent],
        monitor_interval: int = 30,
        max_concurrent: int = 1,
        cache: CacheStore | None = None,
        doc_store: DocumentStore | None = None,
    ):
        super().__init__(config, event_bus)
        # Descending priority: highest-priority agent is tried first in ask().
        self.agents = sorted(agents, key=lambda a: -a.priority)
        self.interval = monitor_interval
        self._max_concurrent = max_concurrent
        # Semaphore created in start() once the event loop is running.
        self._llm_sem: asyncio.Semaphore | None = None
        self._cache = cache
        self._doc = doc_store
        self._agent_detail: dict[str, dict] = {}

    async def ask(self, question: str) -> str:
        """Route a question to the highest-priority agent that has ask()."""
        for agent in self.agents:
            if hasattr(agent, "ask"):
                return await agent.ask(question)
        return "No agent available."

    async def ask_agent(self, agent_name: str, question: str) -> str:
        """Ask a specific agent by name without interrupting the MAS loop."""
        for agent in self.agents:
            if agent.agent_name == agent_name:
                if hasattr(agent, "ask"):
                    return await agent.ask(question)
                return f"Agent '{agent_name}' does not support ask()."
        return f"Agent '{agent_name}' not found. Available: {[a.agent_name for a in self.agents]}"

    def get_agent_detail(self, agent_name: str) -> dict:
        """Return the stored detail snapshot for a specific agent."""
        return self._agent_detail.get(agent_name, {})

    def _sem(self) -> asyncio.Semaphore:
        """The concurrency limiter, created on first use (needs a running loop)."""
        if self._llm_sem is None:
            self._llm_sem = asyncio.Semaphore(self._max_concurrent)
        return self._llm_sem

    async def _run_agent(self, agent: TwinAgent) -> None:
        """Run one full observe→reason→act cycle for a single agent."""
        async with self._sem():
            try:
                obs = await agent.observe()
                findings = await agent.reason(obs)

                now = int(time.time())
                status = {
                    "agent_name": agent.agent_name,
                    "domain":     getattr(agent, "domain", ""),
                    "anomaly":    bool(obs.get("anomaly_detected", False)),
                    "action":     findings.get("action", "monitoring"),
                    "severity":   findings.get("severity", "info"),
                    "detail":     findings.get("summary", ""),
                    "ts_s":       now,
                }

                self._agent_detail[agent.agent_name] = {
                    "agent_name":   agent.agent_name,
                    "domain":       getattr(agent, "domain", ""),
                    "observations": obs,
                    "findings":     findings,
                    "tool_calls":   getattr(agent, "get_last_tool_calls", lambda: [])(),
                    "error":        None,
                    "ts_s":         now,
                }

                if obs.get("anomaly_detected"):
                    await agent.act(findings)
                    await self.bus.publish(
                        DomainEvent(
                            event_type="agent.action",
                            source_layer="intelligent",
                            source_asset=self.config.asset_id,
                            payload=findings,
                        )
                    )

                if self._cache:
                    await self._cache.aset_latest(f"mas_agent_{agent.agent_name}", status)

                if self._doc:
                    # Only persist to MongoDB when something noteworthy happened;
                    # routine monitoring entries would fill the collection too fast.
                    is_noteworthy = (
                        obs.get("anomaly_detected")
                        or status["action"] not in ("no_action", "monitoring")
                    )
                    if is_noteworthy:
                        await self._doc.alog_event(
                            f"mas_agent_{agent.agent_name}",
                            status,
                            severity=status["severity"],
                        )
                        await self._doc.alog_event(
                            f"mas_agent_detail_{agent.agent_name}",
                            _safe_payload({
                                "agent_name":   agent.agent_name,
                                "domain":       getattr(agent, "domain", ""),
                                "anomaly":      bool(obs.get("anomaly_detected", False)),
                                "action":       findings.get("action", "monitoring"),
                                "severity":     findings.get("severity", "info"),
                                "observations": obs,
                                "findings":     findings,
                                "tool_calls":   getattr(agent, "get_last_tool_calls", lambda: [])(),
                                "ts_s":         now,
                            }),
                            severity=status["severity"],
                        )
            except Exception as e:
                self.log.error("Agent '%s' error: %s", agent.agent_name, e)
                self._agent_detail[agent.agent_name] = {
                    "agent_name": agent.agent_name,
                    "domain":     getattr(agent, "domain", ""),
                    "error":      str(e),
                    "ts_s":       int(time.time()),
                }

    async def _handle_escalation(self, question: str, from_state: str, to_state: str) -> None:
        """Triggered by reactive.escalation_requested — routes to the highest-priority agent."""
        self.log.info(
            "Escalation from reactive layer (%s → %s): investigating",
            from_state, to_state,
        )
        try:
            async with self._sem():
                answer = await self.ask(question)
            if self._doc:
                await self._doc.alog_event(
                    "mas_escalation_response",
                    {
                        "from_state": from_state,
                        "to_state":   to_state,
                        "question":   question,
                        "answer":     answer,
                    },
                    severity="warning",
                )
            self.log.info("Escalation response: %s", answer[:200])
        except Exception as e:
            self.log.error("Escalation handler error: %s", e)

    async def start(self) -> None:
        self._running = True
        # Limit concurrent LLM calls to prevent provider rate-limit errors.
        # Default max_concurrent=1 serialises all agent LLM calls.
        self._llm_sem = asyncio.Semaphore(self._max_concurrent)

        # Subscribe to reactive layer escalations so the MAS investigates
        # non-nominal FSM transitions immediately rather than waiting for the
        # next polling cycle. The EventBus dispatches each handler as its own
        # task (fire-and-forget, no awaiting), so the handler is async to do its
        # own awaiting and must not assume any ordering or backpressure.
        async def _on_escalation(event: DomainEvent) -> None:
            if event.source_asset != self.config.asset_id:
                return
            payload = event.payload or {}
            await self._handle_escalation(
                question   = payload.get("question", "Investigate the current state."),
                from_state = payload.get("from_state", "?"),
                to_state   = payload.get("to_state", "?"),
            )

        self.bus.subscribe("reactive.escalation_requested", _on_escalation)

        self.log.info(
            "MAS started with %d agents (max_concurrent=%d)",
            len(self.agents), self._max_concurrent,
        )
        while self._running:
            await asyncio.gather(*[self._run_agent(a) for a in self.agents])
            await asyncio.sleep(self.interval)
