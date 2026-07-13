"""Composite health score calculator."""

from __future__ import annotations

from collections.abc import Mapping


class HealthScoreCalculator:
    """
    Computes a 0–100 composite health score from threshold violations.

    Each field contributes equally; being in warning zone costs 50% of its
    weight and critical costs 100%.

    Pure function of the readings passed in — the caller owns data access, so
    this can run anywhere (async loops, tests, notebooks) without touching a
    store.
    """

    def __init__(self, thresholds: dict):
        self.thresholds = thresholds

    def compute(self, readings: Mapping[str, float | None]) -> float:
        if not self.thresholds:
            return 100.0

        total_penalty = 0.0
        weight_per_field = 100.0 / len(self.thresholds)

        for field, t in self.thresholds.items():
            val = readings.get(field)
            if val is None:
                continue
            low = t.get("low", False)
            crit_t, warn_t = t.get("crit"), t.get("warn")
            in_crit = crit_t is not None and (
                (low and val < crit_t) or (not low and val > crit_t)
            )
            in_warn = warn_t is not None and (
                (low and val < warn_t) or (not low and val > warn_t)
            )
            if in_crit:
                total_penalty += weight_per_field
            elif in_warn:
                total_penalty += weight_per_field * 0.5

        return max(0.0, round(100.0 - total_penalty, 1))
