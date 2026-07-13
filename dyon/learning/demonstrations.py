"""
Domain-agnostic demonstration data abstraction.

A :class:`Demonstrations` bundle is the common currency every Learning-from-
Demonstration algorithm in this package consumes: arrays of transitions plus
the observation/action spaces they live in. A :class:`DemonstrationSource`
produces such a bundle from somewhere (in-memory arrays, a versioned corpus
file, a document store, …).

The bundle converts to the ``imitation`` library's native formats on demand, so
nothing here imports ``imitation`` at module load — twins that only collect or
inspect demonstrations need not pull in the training stack.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from gymnasium import spaces


@dataclass
class Demonstrations:
    """A set of expert transitions with the spaces they were recorded in.

    ``obs``/``next_obs`` are float arrays of shape ``(N, obs_dim)``; ``acts`` is
    ``(N,)`` for discrete actions or ``(N, act_dim)`` for continuous; ``dones``
    is a boolean ``(N,)`` flag marking episode-final transitions. ``episode_ids``
    is optional and, when present, lets :meth:`to_trajectories` regroup the flat
    transitions back into ordered episodes.
    """

    obs: np.ndarray
    acts: np.ndarray
    next_obs: np.ndarray
    dones: np.ndarray
    observation_space: spaces.Space
    action_space: spaces.Space
    episode_ids: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.obs = np.asarray(self.obs, dtype=np.float32)
        self.next_obs = np.asarray(self.next_obs, dtype=np.float32)
        self.dones = np.asarray(self.dones, dtype=bool)
        # Discrete actions stay integer; continuous stay float.
        self.acts = np.asarray(self.acts)
        n = len(self.obs)
        if not (len(self.acts) == len(self.next_obs) == len(self.dones) == n):
            raise ValueError(
                "Demonstrations arrays must be equal length: "
                f"obs={len(self.obs)} acts={len(self.acts)} "
                f"next_obs={len(self.next_obs)} dones={len(self.dones)}"
            )
        if self.episode_ids is not None:
            self.episode_ids = np.asarray(self.episode_ids)
            if len(self.episode_ids) != n:
                raise ValueError("episode_ids must match transition count")

    def __len__(self) -> int:
        return len(self.obs)

    def to_imitation_transitions(self):
        """Convert to ``imitation.data.types.Transitions`` (flat, unordered).

        Suitable for BC and the discriminator of GAIL/AIRL, which consume
        individual transitions rather than whole trajectories.
        """
        from imitation.data.types import Transitions

        return Transitions(
            obs=self.obs,
            acts=self.acts,
            next_obs=self.next_obs,
            dones=self.dones,
            infos=np.array([{} for _ in range(len(self))]),
        )

    def to_trajectories(self):
        """Regroup transitions into ``imitation`` trajectories by episode id.

        Requires ``episode_ids``. Transitions are assumed already time-ordered
        within each episode (the source is responsible for ordering them).
        """
        if self.episode_ids is None:
            raise ValueError(
                "to_trajectories() requires episode_ids to group transitions"
            )
        from imitation.data.types import Trajectory

        trajectories = []
        # Preserve first-seen episode order.
        seen: list[Any] = []
        index: dict[Any, list[int]] = {}
        for i, eid in enumerate(self.episode_ids):
            key = eid.item() if hasattr(eid, "item") else eid
            if key not in index:
                index[key] = []
                seen.append(key)
            index[key].append(i)

        for key in seen:
            rows = index[key]
            ep_obs = self.obs[rows]
            ep_acts = self.acts[rows]
            ep_next_last = self.next_obs[rows][-1]
            # A Trajectory stores T+1 observations (states visited) for T actions.
            obs_seq = np.vstack([ep_obs, ep_next_last[None, :]])
            trajectories.append(
                Trajectory(obs=obs_seq, acts=ep_acts, infos=None, terminal=True)
            )
        return trajectories


class DemonstrationSource(ABC):
    """Produces a :class:`Demonstrations` bundle from some backing store."""

    @abstractmethod
    def load(self) -> Demonstrations:
        ...


class ArrayDemonstrationSource(DemonstrationSource):
    """Wraps already-materialised arrays as a source (tests / in-memory use)."""

    def __init__(
        self,
        *,
        obs: Sequence,
        acts: Sequence,
        next_obs: Sequence,
        dones: Sequence,
        observation_space: spaces.Space,
        action_space: spaces.Space,
        episode_ids: Sequence | None = None,
    ) -> None:
        self._demos = Demonstrations(
            obs=np.asarray(obs),
            acts=np.asarray(acts),
            next_obs=np.asarray(next_obs),
            dones=np.asarray(dones),
            observation_space=observation_space,
            action_space=action_space,
            episode_ids=None if episode_ids is None else np.asarray(episode_ids),
        )

    def load(self) -> Demonstrations:
        return self._demos


class CorpusDemonstrationSource(DemonstrationSource):
    """Loads a demonstration array file from a versioned ``TrainingCorpus``.

    The corpus stores an ``.npz`` with keys ``obs/acts/next_obs/dones`` (and
    optional ``episode_ids``); spaces are supplied by the caller since they are
    not part of the raw array file.
    """

    def __init__(
        self,
        corpus,
        dataset_name: str,
        observation_space: spaces.Space,
        action_space: spaces.Space,
        version: str | None = None,
    ) -> None:
        self._corpus = corpus
        self._dataset = dataset_name
        self._obs_space = observation_space
        self._act_space = action_space
        self._version = version

    def load(self) -> Demonstrations:
        import os
        import tempfile

        version = self._version or self._corpus.get_latest_version(self._dataset)
        if version is None:
            raise FileNotFoundError(f"No versions for dataset '{self._dataset}'")

        tmp = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as f:
                tmp = f.name
            self._corpus.download_version(self._dataset, version, tmp)
            # The archive holds only numeric arrays (obs/acts/next_obs/dones and
            # an optional integer episode_ids), so pickle support is pure attack
            # surface — refuse it.
            data = np.load(tmp, allow_pickle=False)
            return Demonstrations(
                obs=data["obs"],
                acts=data["acts"],
                next_obs=data["next_obs"],
                dones=data["dones"],
                observation_space=self._obs_space,
                action_space=self._act_space,
                episode_ids=data.get("episode_ids", None),
            )
        finally:
            if tmp and os.path.exists(tmp):
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
