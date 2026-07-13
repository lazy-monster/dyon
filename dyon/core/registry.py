"""Central registries for layers, models, and services."""

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class _Registry(Generic[T]):
    def __init__(self, label: str):
        self._items: dict[str, T] = {}
        self._label = label

    def register(self, name: str, item: T) -> None:
        if name in self._items:
            log.warning("%s registry: overwriting '%s'", self._label, name)
        self._items[name] = item

    def get(self, name: str) -> T:
        if name not in self._items:
            raise KeyError(f"{self._label} '{name}' not registered")
        return self._items[name]

    def all(self) -> dict[str, T]:
        return dict(self._items)

    def names(self) -> list[str]:
        return list(self._items.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._items


class LayerRegistry(_Registry[Any]):
    """Registry for DT layer instances."""

    def __init__(self):
        super().__init__("Layer")


class ModelRegistry(_Registry[Any]):
    """Registry for simulation model instances."""

    def __init__(self):
        super().__init__("Model")


# The service registry lives in dyon.services.base (ServiceRegistry there is
# the one create_app uses). It is intentionally NOT duplicated here: core is the
# lowest layer and must not depend on the services layer, so import it directly
# from dyon.services when you need it.
