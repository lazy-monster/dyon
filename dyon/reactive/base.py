"""Reactive layer protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Rule(Protocol):
    """A single reactive rule."""

    rule_name: str

    def evaluate(self, readings: dict[str, float | None]) -> str | None:
        """
        Evaluate the rule against current readings.

        Returns a trigger name ("warning", "critical") or None if not triggered.
        """
        ...


@runtime_checkable
class ReactiveController(Protocol):
    """A controller that produces control outputs."""

    controller_name: str

    def compute(self, process_variable: float | None) -> float | None:
        """Return the control command, or None if no data."""
        ...
