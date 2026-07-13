"""PolicyTrainer: Stable-Baselines3 wrapper for RL policy training."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.autonomous.gym_env import GenericTwinEnv

log = logging.getLogger(__name__)


class PolicyTrainer:
    """
    Wraps Stable-Baselines3 to train an RL policy on a GenericTwinEnv.

    Usage::

        trainer = PolicyTrainer(env, algorithm="SAC", save_dir="./policies")
        trainer.train(total_timesteps=200_000)
        trainer.save("pump_policy")
    """

    def __init__(
        self,
        env: GenericTwinEnv,
        algorithm: str = "SAC",
        save_dir: str = "./policies",
        tensorboard_log: str | None = None,
        **algo_kwargs,
    ):
        from stable_baselines3 import A2C, PPO, SAC, TD3

        self._env = env
        self._save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

        _algo_map = {"SAC": SAC, "TD3": TD3, "PPO": PPO, "A2C": A2C}
        AlgoClass = _algo_map.get(algorithm.upper())
        if AlgoClass is None:
            raise ValueError(f"Unknown algorithm '{algorithm}'. Choose from {list(_algo_map)}")

        self._model = AlgoClass(
            "MlpPolicy",
            env,
            verbose=1,
            tensorboard_log=tensorboard_log,
            **algo_kwargs,
        )
        log.info("PolicyTrainer initialised: %s", algorithm)

    def train(self, total_timesteps: int = 100_000) -> None:
        log.info("Training for %d timesteps ...", total_timesteps)
        self._model.learn(total_timesteps=total_timesteps)
        log.info("Training complete")

    def save(self, name: str = "policy") -> str:
        path = os.path.join(self._save_dir, name)
        self._model.save(path)
        log.info("Policy saved to '%s.zip'", path)
        return f"{path}.zip"

    @property
    def model(self):
        return self._model
