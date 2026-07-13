"""Abstract base for autonomous controllers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AbstractAutonomousController(ABC):
    """Base class for any autonomous control strategy."""

    @abstractmethod
    async def observe(self) -> dict:
        """Collect a comprehensive situation snapshot."""
        ...

    @abstractmethod
    async def orient(self, observation: dict) -> dict:
        """Contextualise the situation against goals."""
        ...

    @abstractmethod
    async def decide(self, observation: dict, assessment: dict) -> dict:
        """Select an action plan."""
        ...

    @abstractmethod
    async def act(self, plan: dict) -> None:
        """Execute the decided plan."""
        ...
