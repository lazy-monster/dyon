"""
Skill-transfer pipeline orchestration.

Chains Learning-from-Demonstration stages — **BC → IRL → RL-on-learned-reward** —
threading each stage's policy into the next, exposing the recovered reward, and
providing validation-gated promotion and corpus versioning. Each stage is
optional, so the same object covers:

* full transfer: ``bc_epochs>0, airl_timesteps>0, final_rl_timesteps>0``
* clone-then-recover: ``final_rl_timesteps=0``
* reward **reuse** under new dynamics: ``bc_epochs=0, airl_timesteps=0,
  reward_path=<saved reward>, final_rl_timesteps>0, final_rl_from_scratch=True``

Everything here is domain-agnostic: it operates on an env + a
:class:`DemonstrationSource` and SB3 policies.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from dyon.learning._util import as_venv
from dyon.learning.reward import LearnedRewardFn

if TYPE_CHECKING:
    from dyon.learning.demonstrations import Demonstrations, DemonstrationSource

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Evaluation helpers
# --------------------------------------------------------------------------- #
def action_match_accuracy(model, demos: Demonstrations) -> float:
    """Fraction of held-out demonstration actions the policy reproduces.

    Defined for discrete action spaces (the natural metric for cloning expert
    decisions). Returns ``nan`` for non-discrete spaces.
    """
    try:
        from gymnasium.spaces import Discrete
    except ImportError:  # pragma: no cover
        from gym.spaces import Discrete  # type: ignore

    if not isinstance(demos.action_space, Discrete):
        return float("nan")
    if len(demos) == 0:
        return float("nan")
    preds, _ = model.predict(demos.obs, deterministic=True)
    preds = np.asarray(preds).reshape(-1)
    truth = np.asarray(demos.acts).reshape(-1)
    return float((preds == truth).mean())


def mean_return(model, env, reward: LearnedRewardFn | None = None,
                n_episodes: int = 10, max_steps: int = 200) -> float:
    """Average episode return of ``model`` in ``env``.

    Uses the learned reward when provided (overlaid via a reward wrapper),
    otherwise the env's native reward.
    """
    venv = as_venv(env)
    if reward is not None:
        venv = reward.wrap_venv(venv)
    total = 0.0
    for _ in range(n_episodes):
        obs = venv.reset()
        ep = 0.0
        for _ in range(max_steps):
            action, _ = model.predict(obs, deterministic=True)
            obs, r, done, _ = venv.step(action)
            ep += float(np.asarray(r).reshape(-1)[0])
            if done[0]:
                break
        total += ep
    return total / max(1, n_episodes)


def split_demonstrations(
    demos: Demonstrations, holdout_frac: float = 0.2, seed: int = 0
):
    """Random transition-level split into (train, holdout) bundles."""
    from dyon.learning.demonstrations import Demonstrations

    n = len(demos)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    n_hold = max(1, int(n * holdout_frac)) if n > 1 else 0
    hold_idx, train_idx = perm[:n_hold], perm[n_hold:]

    def _subset(idx):
        return Demonstrations(
            obs=demos.obs[idx],
            acts=demos.acts[idx],
            next_obs=demos.next_obs[idx],
            dones=demos.dones[idx],
            observation_space=demos.observation_space,
            action_space=demos.action_space,
            episode_ids=None if demos.episode_ids is None else demos.episode_ids[idx],
        )

    if n_hold == 0:
        return demos, None
    return _subset(train_idx), _subset(hold_idx)


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
@dataclass
class PipelineResult:
    model: object
    reward: LearnedRewardFn | None = None
    metrics: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)
    stages_run: list = field(default_factory=list)


class SkillTransferPipeline:
    """Composes BC → AIRL → RL-on-learned-reward over a single env + source."""

    def __init__(
        self,
        *,
        env,
        demonstration_source: DemonstrationSource,
        save_dir: str = "./policies",
        corpus=None,
        dataset_name: str = "skill_policy",
        seed: int = 0,
    ) -> None:
        os.makedirs(save_dir, exist_ok=True)
        self._env = env
        self._source = demonstration_source
        self._save_dir = save_dir
        self._corpus = corpus
        self._dataset = dataset_name
        self._seed = seed

    def run(
        self,
        *,
        bc_epochs: int = 20,
        airl_timesteps: int = 100_000,
        final_rl_timesteps: int = 0,
        final_rl_from_scratch: bool = False,
        reward_path: str | None = None,
        irl_algo: str = "airl",
        allow_variable_horizon: bool = False,
        demos: Demonstrations | None = None,
    ) -> PipelineResult:
        from stable_baselines3 import PPO

        from dyon.learning.imitation_trainers import BCTrainer
        from dyon.learning.irl_trainers import AIRLTrainer, GAILTrainer

        if demos is None:
            demos = self._source.load()
        result = PipelineResult(model=None)

        model = None
        bc_policy = None
        reward: LearnedRewardFn | None = None

        # Stage 1 — BC warm-start.
        if bc_epochs > 0:
            bc = BCTrainer(demonstrations=demos, save_dir=self._save_dir, seed=self._seed)
            bc.train(n_epochs=bc_epochs)
            model = bc.get_sb3_model()
            bc_policy = bc.policy
            result.stages_run.append("bc")

        # Stage 2 — IRL (recover reward + refine), warm-started from BC.
        if airl_timesteps > 0:
            TrainerCls = AIRLTrainer if irl_algo.lower() == "airl" else GAILTrainer
            irl = TrainerCls(
                env=self._env,
                demonstrations=demos,
                init_policy=bc_policy,
                save_dir=self._save_dir,
                allow_variable_horizon=allow_variable_horizon,
            )
            irl.train(total_timesteps=airl_timesteps)
            model = irl.get_sb3_model()
            reward = irl.reward_fn() if irl_algo.lower() == "airl" else None
            result.stages_run.append(irl_algo.lower())
        elif reward_path:
            reward = LearnedRewardFn.load(reward_path)

        # Stage 3 — RL on the (frozen) learned reward.
        if final_rl_timesteps > 0:
            if reward is None:
                raise ValueError(
                    "final RL stage needs a reward (run AIRL or pass reward_path)"
                )
            wrapped = reward.wrap_venv(as_venv(self._env))
            if final_rl_from_scratch or model is None:
                model = PPO("MlpPolicy", wrapped, verbose=0)
            else:
                model.set_env(wrapped)
            model.learn(total_timesteps=final_rl_timesteps)
            result.stages_run.append(
                "rl_scratch" if final_rl_from_scratch else "rl_warm"
            )

        if model is None:
            raise ValueError("Pipeline ran no stages — nothing to return")

        result.model = model
        result.reward = reward
        log.info("Pipeline complete: stages=%s", result.stages_run)
        return result

    # -- promotion ------------------------------------------------------------
    def evaluate(self, model, holdout: Demonstrations,
                 reward: LearnedRewardFn | None = None) -> dict:
        return {
            "action_match": action_match_accuracy(model, holdout),
            "mean_return": mean_return(model, self._env, reward),
        }

    @staticmethod
    def should_promote(new: dict, current: dict | None) -> bool:
        """Promote when the candidate is no worse on action-match and not
        clearly worse on return (current=None ⇒ always promote)."""
        if not current:
            return True

        def _g(d, k):
            v = d.get(k, float("nan"))
            return v if v == v else None  # nan → None

        new_acc, cur_acc = _g(new, "action_match"), _g(current, "action_match")
        if new_acc is not None and cur_acc is not None:
            if new_acc > cur_acc:
                return True
            if new_acc < cur_acc:
                return False
        new_ret, cur_ret = _g(new, "mean_return"), _g(current, "mean_return")
        if new_ret is not None and cur_ret is not None:
            return new_ret >= cur_ret
        return True

    # -- versioning -----------------------------------------------------------
    def version(self, result: PipelineResult, metadata: dict | None = None) -> str | None:
        """Persist the policy (and reward) to the ``TrainingCorpus`` if set."""
        if self._corpus is None:
            return None
        import tempfile

        meta = dict(metadata or {})
        meta["stages_run"] = result.stages_run
        meta.update(result.metrics)

        with tempfile.TemporaryDirectory() as tmp:
            policy_zip = os.path.join(tmp, "policy")
            result.model.save(policy_zip)  # type: ignore[attr-defined]
            version = self._corpus.push_version(
                self._dataset, f"{policy_zip}.zip", metadata=meta
            )
            if result.reward is not None:
                reward_pt = os.path.join(tmp, "reward.pt")
                result.reward.save(reward_pt)
                self._corpus.push_version(
                    f"{self._dataset}_reward", reward_pt,
                    metadata={"policy_version": version},
                )
        log.info("Pipeline result versioned as %s/%s", self._dataset, version)
        return version


class SyncTrigger:
    """Fires when enough new demonstrations accrue OR an interval elapses."""

    def __init__(self, *, demo_threshold: int = 100, interval_seconds: float = 86_400.0):
        self._threshold = demo_threshold
        self._interval = interval_seconds
        self._last_count = 0
        self._last_time = time.time()

    def should_fire(self, current_demo_count: int) -> tuple[bool, str]:
        if current_demo_count - self._last_count >= self._threshold:
            return True, "demo_threshold"
        if time.time() - self._last_time >= self._interval:
            return True, "interval"
        return False, ""

    def mark_fired(self, current_demo_count: int) -> None:
        self._last_count = current_demo_count
        self._last_time = time.time()
