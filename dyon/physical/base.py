"""Abstract base classes for physical layer publishers and simulators."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AbstractPublisher(ABC):
    """Publishes sensor readings to the network layer."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def publish_reading(self, fields: dict[str, float]) -> None: ...


class AbstractSimulator(ABC):
    """Generates synthetic sensor readings for testing and development."""

    @abstractmethod
    def step(self, dt: float = 1.0) -> dict[str, float]:
        """Advance simulation by dt seconds and return new readings."""
        ...

    @abstractmethod
    def reset(self) -> None: ...

    @property
    @abstractmethod
    def is_running(self) -> bool: ...
