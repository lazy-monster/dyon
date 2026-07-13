"""GenericTwinEnv.reset must return a real initial observation (assessment §4.3).

The old reset() returned an all-zeros vector, which ignores the model's initial
state and can violate the observation Box bounds when obs_low > 0.
"""

from __future__ import annotations

import numpy as np

from dyon.autonomous.gym_env import GenericTwinEnv


class _ToyModel:
    model_name = "toy"
    model_type = "ode"

    def reset(self):
        pass

    def step(self, dt, inputs):
        # pv depends on the control so the neutral mid-range action is observable
        return {"pv": 5.0 + inputs.get("ctrl", 0.0)}


def _env():
    return GenericTwinEnv(
        model=_ToyModel(),
        control_field="ctrl",
        process_variable="pv",
        target=5.0,
        ctrl_min=0.0,
        ctrl_max=10.0,
        obs_fields=["pv"],
        obs_low=np.array([1.0]),     # > 0, so an all-zeros obs would be invalid
        obs_high=np.array([20.0]),
        max_steps=10,
    )


def test_reset_primes_real_observation_within_bounds():
    env = _env()
    obs, _info = env.reset()
    # Neutral action 0 -> mid-range ctrl 5.0 -> pv = 10.0
    assert obs[0] == 10.0
    assert env.observation_space.contains(obs)   # would fail for all-zeros


def test_reset_step_count_is_zero():
    env = _env()
    env.reset()
    assert env._step_count == 0
