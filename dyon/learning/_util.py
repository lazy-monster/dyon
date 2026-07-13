"""Shared helpers for the learning package."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from gymnasium import spaces


def _gym():
    try:
        import gymnasium as gym
        return gym
    except ImportError:  # pragma: no cover
        import gym  # type: ignore
        return gym


def make_space_venv(observation_space: spaces.Space, action_space: spaces.Space):
    """A 1-env ``DummyVecEnv`` exposing only the given spaces.

    Used to host an SB3 policy as a savable PPO container when no real
    environment is available (e.g. behavioural cloning, which needs only the
    spaces). ``step``/``reset`` are never exercised for training.
    """
    from stable_baselines3.common.vec_env import DummyVecEnv

    gym = _gym()

    # Spaces such as Text and Graph report no shape; the stub observation is
    # never consumed, so a scalar array stands in for them.
    obs_shape = observation_space.shape or ()
    obs_dtype = observation_space.dtype

    class _Env(gym.Env):  # type: ignore[name-defined]
        metadata: dict = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self.observation_space = observation_space
            self.action_space = action_space

        def reset(self, *, seed=None, options=None):
            return np.zeros(obs_shape, dtype=obs_dtype), {}

        def step(self, action):
            return (
                np.zeros(obs_shape, dtype=obs_dtype),
                0.0,
                True,
                False,
                {},
            )

    return DummyVecEnv([_Env])


def as_venv(env):
    """Return a ``VecEnv`` for ``env``, wrapping a bare Gym env if needed."""
    from stable_baselines3.common.vec_env import DummyVecEnv, VecEnv

    if isinstance(env, VecEnv):
        return env
    return DummyVecEnv([lambda: env])
