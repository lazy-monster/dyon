"""GenericTwinEnv: Gymnasium environment wrapping any TwinModel."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import gymnasium as gym
import numpy as np
from gymnasium import spaces

if TYPE_CHECKING:
    from dyon.simulation.base import TwinModel

log = logging.getLogger(__name__)


class GenericTwinEnv(gym.Env):
    """
    Gymnasium environment that wraps any TwinModel.

    Configuration:
        model           — TwinModel instance to use as the environment
        control_field   — name of the variable the agent adjusts
        process_variable— name of the variable being regulated
        target          — desired value for process_variable
        ctrl_min/max    — valid range for control output
        obs_fields      — field names forming the observation
        obs_low/high    — bounds for observation space
        reward_fn       — optional custom reward function(obs, target) → float
        max_steps       — episode length
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        model: TwinModel,
        control_field: str,
        process_variable: str,
        target: float,
        ctrl_min: float,
        ctrl_max: float,
        obs_fields: list[str],
        obs_low: np.ndarray,
        obs_high: np.ndarray,
        reward_fn: Callable | None = None,
        max_steps: int = 500,
    ):
        super().__init__()
        self.model = model
        self.control_field = control_field
        self.process_variable = process_variable
        self.target = target
        self.ctrl_min = ctrl_min
        self.ctrl_max = ctrl_max
        self.obs_fields = obs_fields
        self.reward_fn = reward_fn or self._default_reward
        self.max_steps = max_steps
        self._step_count = 0
        self._last_obs: dict[str, float] = {}

        self.observation_space = spaces.Box(
            obs_low.astype(np.float32),
            obs_high.astype(np.float32),
            dtype=np.float32,
        )
        self.action_space = spaces.Box(
            np.array([-1.0], dtype=np.float32),
            np.array([1.0], dtype=np.float32),
            shape=(1,),
            dtype=np.float32,
        )

    def _default_reward(self, obs: dict, target: float) -> float:
        # Prefer the simulator-prefixed value but fall back to the bare field.
        # ``or`` chains would mis-fire when the value is a legitimate 0.0, so
        # check membership / None explicitly.
        pv = obs.get(f"sim_{self.process_variable}")
        if pv is None:
            pv = obs.get(self.process_variable)
        if pv is None:
            pv = 0.0
        return -abs(pv - target)

    def _scale_action(self, action: np.ndarray) -> float:
        """Map [-1, 1] → [ctrl_min, ctrl_max]."""
        a = float(np.clip(action[0], -1.0, 1.0))
        return self.ctrl_min + (a + 1.0) / 2.0 * (self.ctrl_max - self.ctrl_min)

    def _get_obs(self) -> np.ndarray:
        return np.array(
            [self._last_obs.get(f, 0.0) for f in self.obs_fields],
            dtype=np.float32,
        )

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.model.reset()
        self._step_count = 0
        # Prime the first observation from a real model step at the neutral
        # action (0 → mid-range control), instead of returning an all-zeros
        # vector: zeros ignore the model's actual initial state and can fall
        # outside the observation Box bounds when obs_low > 0.
        neutral_ctrl = self._scale_action(np.array([0.0], dtype=np.float32))
        self._last_obs = self.model.step(
            dt=1.0, inputs={self.control_field: neutral_ctrl}
        )
        obs = self._get_obs()
        return obs, {}

    def step(self, action: np.ndarray):
        ctrl = self._scale_action(action)
        outputs = self.model.step(dt=1.0, inputs={self.control_field: ctrl})
        self._last_obs = outputs
        obs = self._get_obs()
        reward = self.reward_fn(outputs, self.target)
        self._step_count += 1
        terminated = self._step_count >= self.max_steps
        return obs, reward, terminated, False, {}

    def render(self): ...
    def close(self): ...
