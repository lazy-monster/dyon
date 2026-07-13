"""
Learned-reward adapter.

IRL recovers a reward *network*; this module wraps it so the recovered reward
can be reused the same way a hand-written one would be:

* ``wrap_venv`` — overlay the learned reward on a vectorised env so an ordinary
  RL algorithm (PPO/SAC/…) can optimise against it. This is the canonical
  "train a policy on the recovered reward" path (e.g. the final stage of a
  skill-transfer pipeline, or transferring the reward to a *new* environment).
* ``make_generic_reward_fn`` — a state-only adapter matching the
  ``reward_fn(obs, target) -> float`` signature that
  :class:`dyon.autonomous.gym_env.GenericTwinEnv` accepts, so a learned
  reward can drop straight into the framework's generic RL env.
* ``__call__`` — score individual transitions, for inspection / metrics.

The wrapped object is a plain ``imitation`` ``RewardNet`` (a torch module), so
it saves and loads with ``torch``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from stable_baselines3.common.vec_env import VecEnv

log = logging.getLogger(__name__)


class LearnedRewardFn:
    """Reusable wrapper around an IRL-recovered ``RewardNet``."""

    def __init__(self, reward_net) -> None:
        self._net = reward_net

    @property
    def reward_net(self):
        return self._net

    def predict(
        self,
        state: np.ndarray,
        action: np.ndarray,
        next_state: np.ndarray,
        done: np.ndarray,
    ) -> np.ndarray:
        """Vectorised reward for a batch of transitions."""
        return self._net.predict(state, action, next_state, done)

    def as_reward_fn(self) -> Callable:
        """Return an ``imitation`` ``RewardFn`` callable (used by wrappers)."""
        return self._net.predict

    def wrap_venv(self, venv: VecEnv) -> VecEnv:
        """Overlay the learned reward on ``venv`` for policy optimisation."""
        from imitation.rewards.reward_wrapper import RewardVecEnvWrapper

        return RewardVecEnvWrapper(venv, self._net.predict)

    def make_generic_reward_fn(
        self, obs_fields: Sequence[str]
    ) -> Callable[[dict, float], float]:
        """Adapt to ``GenericTwinEnv``'s ``reward_fn(obs_dict, target)`` hook.

        State-only: assembles the observation vector from ``obs_dict`` using
        ``obs_fields`` (falling back to the bare field name, mirroring
        ``GenericTwinEnv._default_reward``), and evaluates the reward net with a
        zero action and the same state as next-state. Suitable only for reward
        nets configured ``use_action=False, use_next_state=False``.
        """
        fields = list(obs_fields)

        def _reward_fn(obs: dict, target: float) -> float:
            vec = []
            for f in fields:
                v = obs.get(f"sim_{f}")
                if v is None:
                    v = obs.get(f)
                vec.append(float(v) if v is not None else 0.0)
            state = np.array([vec], dtype=np.float32)
            action = np.zeros((1, 1), dtype=np.float32)
            done = np.array([False])
            r = self._net.predict(state, action, state, done)
            return float(np.asarray(r).reshape(-1)[0])

        return _reward_fn

    def __call__(
        self,
        state: np.ndarray,
        action: np.ndarray | None = None,
        next_state: np.ndarray | None = None,
        done: np.ndarray | None = None,
    ) -> np.ndarray:
        state = np.asarray(state, dtype=np.float32)
        if state.ndim == 1:
            state = state[None, :]
        if action is None:
            action = np.zeros((len(state), 1), dtype=np.float32)
        if next_state is None:
            next_state = state
        if done is None:
            done = np.zeros((len(state),), dtype=bool)
        return self._net.predict(state, action, next_state, done)

    def save(self, path: str) -> str:
        """Persist the reward net (whole module) to ``path``."""
        import torch

        torch.save(self._net, path)
        log.info("Learned reward net saved → '%s'", path)
        return path

    @classmethod
    def load(cls, path: str, device: str = "auto") -> LearnedRewardFn:
        """Load a reward net from ``path``.

        This deserializes a pickle-format artifact (``torch.load`` with
        ``weights_only=False``) and must only be given files obtained through
        :meth:`dyon.ml.corpus.TrainingCorpus.download_version`, which verifies
        the manifest checksum before returning the file.
        """
        import torch

        dev = device
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        net = torch.load(path, map_location=dev, weights_only=False)
        return cls(net)
