"""Intelligent layer protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TwinAgent(Protocol):
    """An intelligent agent that monitors and reasons about the twin."""

    agent_name: str
    domain: str       # e.g. "thermal", "mechanical", "predictive"
    priority: int     # higher = invoked first

    async def observe(self) -> dict:
        """Gather current observations from the twin."""
        ...

    async def reason(self, observations: dict) -> dict:
        """Analyse observations and produce findings."""
        ...

    async def act(self, findings: dict) -> None:
        """Take actions based on findings (log, alert, adjust)."""
        ...


class AgentRegistry:
    """Registry for TwinAgent instances."""

    def __init__(self):
        self._agents: dict[str, TwinAgent] = {}

    def register(self, agent: TwinAgent) -> None:
        self._agents[agent.agent_name] = agent

    def get(self, name: str) -> TwinAgent:
        return self._agents[name]

    def all_by_priority(self) -> list[TwinAgent]:
        return sorted(self._agents.values(), key=lambda a: -a.priority)
