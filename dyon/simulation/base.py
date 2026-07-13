"""TwinModel protocol — any model that can be stepped forward in time."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TwinModel(Protocol):
    """Any model that can be stepped forward in time."""

    model_name: str
    model_type: str   # see ModelType enum

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        """Advance by dt seconds given current inputs. Return predicted fields."""
        ...

    def reset(self) -> None:
        """Reset to initial conditions."""
        ...
