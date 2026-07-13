"""
Domain-agnostic feature-schema helper.

A ``FeatureSpec`` is the single source of truth for an observation vector: it
builds both the numeric vector *and* the matching ``gymnasium.spaces.Box`` from
the same ordered list of column definitions, so the observation, the space, and
anything that logs/replays the vector can never silently drift out of sync.

Nothing here knows about any domain — a twin declares its own columns and
extractor callables. The extractor receives whatever context object the caller
passes to :meth:`FeatureSpec.encode` (a dict, a session context, …).

Example::

    spec = FeatureSpec(
        version=2,
        columns=[
            Scalar("sentiment", lambda c: c["sentiment"], 0.0, 1.0),
            Categorical("intent", lambda c: c.get("intent", "neutral"),
                        ["price", "need", "neutral"], one_hot=True),
        ],
    )
    box = spec.box()          # gymnasium Box of shape (len(spec),)
    vec = spec.encode(ctx)    # np.ndarray, same order as spec.names
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np


class FeatureColumn(ABC):
    """One named, bounded contributor to an observation vector.

    A column may expand to several numeric slots (e.g. a one-hot categorical),
    so ``names``/``bounds``/``encode`` all return lists of equal length.
    """

    name: str

    @abstractmethod
    def names(self) -> list[str]:
        """Slot names this column produces, in order."""
        ...

    @abstractmethod
    def bounds(self) -> tuple[list[float], list[float]]:
        """(lows, highs) for each slot, in order."""
        ...

    @abstractmethod
    def encode(self, ctx: Any) -> list[float]:
        """Numeric values for each slot, in order."""
        ...


@dataclass
class Scalar(FeatureColumn):
    """A single continuous feature.

    ``extract`` returns a number; values are clipped into ``[low, high]`` when
    ``clip`` is set so a stray out-of-range reading cannot violate the Box.
    """

    name: str
    extract: Callable[[Any], float]
    low: float
    high: float
    clip: bool = True

    def names(self) -> list[str]:
        return [self.name]

    def bounds(self) -> tuple[list[float], list[float]]:
        return [self.low], [self.high]

    def encode(self, ctx: Any) -> list[float]:
        try:
            v = float(self.extract(ctx))
        except (TypeError, ValueError):
            v = 0.0
        if not np.isfinite(v):
            v = 0.0
        if self.clip:
            v = min(max(v, self.low), self.high)
        return [v]


@dataclass
class Categorical(FeatureColumn):
    """A categorical feature, encoded either one-hot or as a normalised index.

    ``extract`` returns a category label. Unknown labels encode to the all-zero
    vector (one-hot) or to ``0.0`` (index), so unseen categories degrade
    gracefully rather than raising.
    """

    name: str
    extract: Callable[[Any], Any]
    categories: list[str]
    one_hot: bool = True

    def _index(self, ctx: Any) -> int:
        try:
            return self.categories.index(self.extract(ctx))
        except (ValueError, TypeError):
            return -1

    def names(self) -> list[str]:
        if self.one_hot:
            return [f"{self.name}__{c}" for c in self.categories]
        return [self.name]

    def bounds(self) -> tuple[list[float], list[float]]:
        if self.one_hot:
            n = len(self.categories)
            return [0.0] * n, [1.0] * n
        # Normalised index in [0, 1].
        return [0.0], [1.0]

    def encode(self, ctx: Any) -> list[float]:
        idx = self._index(ctx)
        if self.one_hot:
            vec = [0.0] * len(self.categories)
            if idx >= 0:
                vec[idx] = 1.0
            return vec
        if idx < 0 or len(self.categories) <= 1:
            return [0.0]
        return [idx / (len(self.categories) - 1)]


class FeatureSpec:
    """An ordered, versioned collection of :class:`FeatureColumn` s."""

    def __init__(self, columns: list[FeatureColumn], *, version: int) -> None:
        if not columns:
            raise ValueError("FeatureSpec needs at least one column")
        self.columns = columns
        self.version = version

    @property
    def names(self) -> list[str]:
        out: list[str] = []
        for col in self.columns:
            out.extend(col.names())
        return out

    def __len__(self) -> int:
        return len(self.names)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        lows: list[float] = []
        highs: list[float] = []
        for col in self.columns:
            lo, hi = col.bounds()
            lows.extend(lo)
            highs.extend(hi)
        return (
            np.array(lows, dtype=np.float32),
            np.array(highs, dtype=np.float32),
        )

    def box(self):
        """Build the matching ``gymnasium.spaces.Box``."""
        try:
            from gymnasium import spaces
        except ImportError:  # pragma: no cover - fallback for legacy gym
            from gym import spaces  # type: ignore
        low, high = self.bounds()
        return spaces.Box(low=low, high=high, dtype=np.float32)

    def encode(self, ctx: Any) -> np.ndarray:
        """Build the observation vector for ``ctx`` in column order."""
        out: list[float] = []
        for col in self.columns:
            out.extend(col.encode(ctx))
        return np.array(out, dtype=np.float32)
