"""
Adversarial IRL / imitation trainers: AIRL and GAIL.

Both train a generator policy (PPO) against a discriminator that tells expert
transitions from generated ones, using the env only for *dynamics* — the reward
becomes learned.

* **AIRL** structures the discriminator so a reusable, transferable reward
  network falls out. ``reward_fn()`` returns it as a :class:`LearnedRewardFn`,
  which can be saved, inspected, and used to optimise a fresh policy (even under
  different dynamics). This is true inverse RL.
* **GAIL** matches expert behaviour strongly but its discriminator is *not* a
  reusable reward — use it for imitation, not reward recovery.

A generator can be **warm-started** from a behaviourally-cloned policy
(``init_policy``), which makes adversarial training converge faster and more
stably.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from dyon.learning._util import as_venv
from dyon.learning.reward import LearnedRewardFn

if TYPE_CHECKING:
    from dyon.learning.demonstrations import Demonstrations

log = logging.getLogger(__name__)


def _build_ppo(venv, **ppo_kwargs):
    from stable_baselines3 import PPO

    return PPO("MlpPolicy", venv, verbose=0, **ppo_kwargs)


class _AdversarialTrainer:
    """Shared scaffolding for AIRL/GAIL."""

    _algo_name = "adversarial"

    def __init__(
        self,
        *,
        env,
        demonstrations: Demonstrations,
        gen_algo=None,
        init_policy=None,
        reward_net=None,
        demo_batch_size: int | None = None,
        save_dir: str = "./policies",
        gen_kwargs: dict | None = None,
        allow_variable_horizon: bool = False,
        **algo_kwargs,
    ) -> None:
        os.makedirs(save_dir, exist_ok=True)
        self._save_dir = save_dir
        self._venv = as_venv(env)
        self._demos = demonstrations

        self._gen_algo = gen_algo or _build_ppo(self._venv, **(gen_kwargs or {}))

        # Warm-start the generator policy from a cloned policy (state_dict copy).
        if init_policy is not None:
            try:
                self._gen_algo.policy.load_state_dict(init_policy.state_dict())
                log.info("%s generator warm-started from init_policy", self._algo_name)
            except Exception as e:  # arch mismatch — fall back to fresh weights
                log.warning("Warm-start failed (%s); using fresh generator", e)

        self._reward_net = reward_net or self._default_reward_net()

        n = len(demonstrations)
        if demo_batch_size is None:
            demo_batch_size = min(n, 1024)
        demo_batch_size = max(1, min(demo_batch_size, n))

        self._trainer = self._make_trainer(
            demonstrations=demonstrations.to_imitation_transitions(),
            demo_batch_size=demo_batch_size,
            venv=self._venv,
            gen_algo=self._gen_algo,
            reward_net=self._reward_net,
            allow_variable_horizon=allow_variable_horizon,
            **algo_kwargs,
        )
        log.info(
            "%s initialised: %d demos, demo_batch_size=%d",
            self._algo_name, n, demo_batch_size,
        )

    # -- subclass hooks -------------------------------------------------------
    def _default_reward_net(self):
        raise NotImplementedError

    def _make_trainer(self, **kwargs):
        raise NotImplementedError

    # -- common interface -----------------------------------------------------
    def train(self, total_timesteps: int = 100_000, callback=None) -> None:
        log.info("%s training for %d timesteps ...", self._algo_name, total_timesteps)
        self._trainer.train(total_timesteps, callback=callback)
        log.info("%s training complete", self._algo_name)

    @property
    def reward_net(self):
        return self._reward_net

    def reward_fn(self) -> LearnedRewardFn:
        """The recovered reward as a reusable :class:`LearnedRewardFn`."""
        return LearnedRewardFn(self._reward_net)

    def get_sb3_model(self):
        """The trained generator (an SB3 PPO)."""
        return self._gen_algo

    def save(self, name: str) -> dict:
        """Save the generator policy (.zip) and the reward net (.pt).

        Returns a dict of the written paths.
        """
        policy_path = os.path.join(self._save_dir, name)
        self._gen_algo.save(policy_path)
        reward_path = os.path.join(self._save_dir, f"{name}_reward.pt")
        LearnedRewardFn(self._reward_net).save(reward_path)
        log.info("%s saved policy → '%s.zip', reward → '%s'",
                 self._algo_name, policy_path, reward_path)
        return {"policy": f"{policy_path}.zip", "reward": reward_path}


class AIRLTrainer(_AdversarialTrainer):
    """Adversarial IRL — recovers a reusable reward + a generator policy."""

    _algo_name = "AIRL"

    def _default_reward_net(self):
        from imitation.rewards.reward_nets import BasicShapedRewardNet
        from imitation.util.networks import RunningNorm

        return BasicShapedRewardNet(
            self._venv.observation_space,
            self._venv.action_space,
            normalize_input_layer=RunningNorm,
        )

    def _make_trainer(self, **kwargs):
        from imitation.algorithms.adversarial.airl import AIRL

        return AIRL(**kwargs)


class GAILTrainer(_AdversarialTrainer):
    """GAIL — adversarial imitation; discriminator is NOT a reusable reward."""

    _algo_name = "GAIL"

    def _default_reward_net(self):
        from imitation.rewards.reward_nets import BasicRewardNet
        from imitation.util.networks import RunningNorm

        return BasicRewardNet(
            self._venv.observation_space,
            self._venv.action_space,
            normalize_input_layer=RunningNorm,
        )

    def _make_trainer(self, **kwargs):
        from imitation.algorithms.adversarial.gail import GAIL

        return GAIL(**kwargs)
