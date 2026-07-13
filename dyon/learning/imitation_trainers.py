"""
Imitation-learning trainers: Behavioural Cloning and DAgger.

These copy expert behaviour. ``BCTrainer`` is pure supervised learning over
demonstrations (no environment needed); ``DAggerTrainer`` interleaves rollouts
with on-policy expert relabelling to fix BC's compounding-error drift, and
therefore needs an interactive expert.

All trainers produce a Stable-Baselines3 ``PPO`` container so the resulting
policy saves/loads uniformly with the RL and IRL trainers (and the existing
``PolicyDeployer`` / domain policy wrappers).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from dyon.learning._util import as_venv, make_space_venv

if TYPE_CHECKING:
    from dyon.learning.demonstrations import Demonstrations

log = logging.getLogger(__name__)


def _new_ppo_container(venv):
    """A PPO that exists only to host/save an MlpPolicy (never ``.learn``-ed)."""
    from stable_baselines3 import PPO

    return PPO("MlpPolicy", venv, verbose=0)


class BCTrainer:
    """Behavioural Cloning over a :class:`Demonstrations` bundle.

    Wraps ``imitation.algorithms.bc.BC``. The trained policy is hosted in a PPO
    container so ``save()`` writes a standard SB3 ``.zip``.

    Usage::

        bc = BCTrainer(demonstrations=demos, save_dir="./policies")
        bc.train(n_epochs=20)
        bc.save("cloned_policy")
        model = bc.get_sb3_model()      # warm-start an IRL generator with this
    """

    def __init__(
        self,
        *,
        demonstrations: Demonstrations,
        save_dir: str = "./policies",
        seed: int = 0,
        batch_size: int = 32,
        host_model=None,
        **bc_kwargs,
    ) -> None:
        from imitation.algorithms import bc

        os.makedirs(save_dir, exist_ok=True)
        self._save_dir = save_dir
        self._demos = demonstrations

        # Host policy in a PPO container (re-use a caller-supplied one for
        # warm-starting an existing model in place).
        self._sb3 = host_model or _new_ppo_container(
            make_space_venv(
                demonstrations.observation_space, demonstrations.action_space
            )
        )

        # imitation needs the batch_size to not exceed the dataset.
        n = len(demonstrations)
        batch_size = max(1, min(batch_size, n))

        self._bc = bc.BC(
            observation_space=demonstrations.observation_space,
            action_space=demonstrations.action_space,
            demonstrations=demonstrations.to_imitation_transitions(),
            rng=np.random.default_rng(seed),
            policy=self._sb3.policy,
            batch_size=batch_size,
            **bc_kwargs,
        )
        log.info("BCTrainer initialised on %d demonstrations", n)

    def train(
        self, n_epochs: int = 10, on_epoch_end: Callable[[], None] | None = None
    ) -> None:
        log.info("BC training for %d epochs ...", n_epochs)
        self._bc.train(n_epochs=n_epochs, on_epoch_end=on_epoch_end, progress_bar=False)
        log.info("BC training complete")

    @property
    def policy(self):
        return self._bc.policy

    def get_sb3_model(self):
        """The PPO container holding the cloned policy weights."""
        return self._sb3

    def save(self, name: str = "bc_policy") -> str:
        path = os.path.join(self._save_dir, name)
        self._sb3.save(path)
        log.info("BC policy saved → '%s.zip'", path)
        return f"{path}.zip"


class DAggerTrainer:
    """DAgger: BC with on-policy expert relabelling (needs an interactive expert).

    Wraps ``imitation.algorithms.dagger.SimpleDAggerTrainer``. ``expert_policy``
    is any callable/SB3 policy that, given observations, returns the expert
    action — in practice a simulator oracle or a stronger policy that can label
    the states the learner actually visits.
    """

    def __init__(
        self,
        *,
        env,
        expert_policy,
        demonstrations: Demonstrations | None = None,
        scratch_dir: str = "./policies/dagger",
        save_dir: str = "./policies",
        seed: int = 0,
        **bc_kwargs,
    ) -> None:
        from imitation.algorithms import bc
        from imitation.algorithms.dagger import SimpleDAggerTrainer

        os.makedirs(save_dir, exist_ok=True)
        self._save_dir = save_dir
        self._venv = as_venv(env)
        rng = np.random.default_rng(seed)

        bc_trainer = bc.BC(
            observation_space=self._venv.observation_space,
            action_space=self._venv.action_space,
            rng=rng,
            **bc_kwargs,
        )
        self._dagger = SimpleDAggerTrainer(
            venv=self._venv,
            scratch_dir=scratch_dir,
            expert_policy=expert_policy,
            bc_trainer=bc_trainer,
            rng=rng,
        )
        log.info("DAggerTrainer initialised")

    def train(self, total_timesteps: int = 20_000) -> None:
        log.info("DAgger training for %d timesteps ...", total_timesteps)
        self._dagger.train(total_timesteps)
        log.info("DAgger training complete")

    @property
    def policy(self):
        return self._dagger.policy

    def save(self, name: str = "dagger_policy") -> str:
        path = os.path.join(self._save_dir, name)
        self._dagger.policy.save(path)
        log.info("DAgger policy saved → '%s'", path)
        return path
