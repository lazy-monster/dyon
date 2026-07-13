"""Goal planner for the autonomous layer."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Goal:
    name: str
    description: str
    priority: int = 5
    success_condition: str = ""          # e.g. "health_score > 80"
    metadata: dict[str, Any] = field(default_factory=dict)


class GoalPlanner:
    """
    Manages a set of goals and assesses progress toward them.

    Goals are simple data objects; assessment is driven by the current
    observation snapshot returned by OODALoop.observe().
    """

    def __init__(self, goals: list[Goal] | None = None):
        self.goals = sorted(goals or [], key=lambda g: -g.priority)

    def add_goal(self, goal: Goal) -> None:
        self.goals.append(goal)
        self.goals.sort(key=lambda g: -g.priority)

    async def assess(self, observation: dict) -> dict:
        """Assess goal progress and return situational assessment."""
        # ``or`` chain would mis-treat a legitimate health=0.0 as missing and
        # silently flip it to 100.0 — explicit None-check preserves the value.
        health = observation.get("health")
        if health is None:
            health = 100.0
        state = observation.get("state", "running")

        assessment: dict = {
            "health_trend": "stable",
            "risk_level": "low",
            "requires_intervention": False,
            "requires_human_intervention": False,
            "requires_shutdown": False,
            "needs_external_data": False,
        }

        if state == "shutdown":
            assessment["risk_level"] = "critical"
            assessment["requires_intervention"] = True
            assessment["requires_human_intervention"] = True
            assessment["reason"] = "Asset is in shutdown state"

        elif state == "warning" or (isinstance(health, int | float) and health < 50.0):
            assessment["risk_level"] = "high"
            assessment["requires_intervention"] = True
            # ``health`` may be None or a non-numeric string when only ``state``
            # triggered the branch — avoid the ``.1f`` format raising TypeError.
            health_str = f"{health:.1f}" if isinstance(health, int | float) else str(health)
            assessment["reason"] = f"Health score is low ({health_str}) or in warning state"

        elif isinstance(health, int | float) and health < 75.0:
            assessment["risk_level"] = "medium"

        recent_events = observation.get("recent_events", [])
        critical_count = sum(
            1 for e in recent_events if e.get("severity") == "critical"
        )
        if critical_count >= 3:
            assessment["risk_level"] = "critical"
            assessment["requires_human_intervention"] = True
            assessment["reason"] = f"{critical_count} critical events in recent history"

        return assessment
