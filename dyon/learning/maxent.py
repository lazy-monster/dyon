"""
Maximum-Entropy Inverse Reinforcement Learning (classic, dependency-free).

Implements the sampling-based MaxEnt IRL gradient — the Relative-Entropy IRL
formulation (Boularias, Kober & Peters, 2011), which is the practical
maximum-entropy variant that needs neither known dynamics nor an inner RL loop:

    ∇L(θ) = E_expert[∇ r_θ(s)] − E_background[ w(τ) · ∇ r_θ(s) ]

where the background trajectories are rolled out under a reference (random)
policy and importance-weighted by ``w(τ) ∝ exp(Σ_t r_θ(s_t))``. The recovered
reward is **linear over the observation features** (the classic MaxEnt form),
returned as a reusable :class:`LearnedRewardFn`.

This module deliberately avoids the ``imitation`` library so it stands alone as
the textbook reference implementation.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import numpy as np

from dyon.learning._util import as_venv
from dyon.learning.reward import LearnedRewardFn

if TYPE_CHECKING:
    from dyon.learning.demonstrations import Demonstrations

log = logging.getLogger(__name__)


def _torch():
    import torch
    return torch


class LinearRewardNet:
    """A linear reward over observation features: ``r(s) = w·s + b``.

    Exposes the ``predict(state, action, next_state, done)`` signature so it
    plugs into :class:`LearnedRewardFn` (and therefore the reward-vec-env
    wrapper) exactly like an ``imitation`` reward net, and is ``torch.save``-able.
    """

    def __init__(self, obs_dim: int) -> None:
        torch = _torch()
        self.module = torch.nn.Linear(obs_dim, 1)
        self.obs_dim = obs_dim

    def reward_tensor(self, state):
        return self.module(state).squeeze(-1)

    def predict(self, state, action, next_state, done) -> np.ndarray:
        torch = _torch()
        with torch.no_grad():
            s = torch.as_tensor(np.asarray(state, dtype=np.float32))
            if s.ndim == 1:
                s = s[None, :]
            return self.reward_tensor(s).cpu().numpy()


def _rollout_background(venv, n_episodes: int, max_steps: int):
    """Collect random-policy trajectories: list of obs arrays per episode."""
    trajectories: list[np.ndarray] = []
    for _ in range(n_episodes):
        obs = venv.reset()
        states = [np.asarray(obs[0], dtype=np.float32)]
        for _ in range(max_steps):
            action = [venv.action_space.sample()]
            obs, _, done, _ = venv.step(action)
            states.append(np.asarray(obs[0], dtype=np.float32))
            if done[0]:
                break
        trajectories.append(np.array(states, dtype=np.float32))
    return trajectories


def _expert_trajectories(demos: Demonstrations) -> list[np.ndarray]:
    """Group expert transitions into per-episode obs arrays."""
    if demos.episode_ids is None:
        # Treat the whole set as one trajectory.
        return [np.asarray(demos.obs, dtype=np.float32)]
    out: list[np.ndarray] = []
    order: list = []
    idx: dict = {}
    for i, eid in enumerate(demos.episode_ids):
        key = eid.item() if hasattr(eid, "item") else eid
        if key not in idx:
            idx[key] = []
            order.append(key)
        idx[key].append(i)
    for key in order:
        out.append(np.asarray(demos.obs[idx[key]], dtype=np.float32))
    return out


class MaxEntIRLTrainer:
    """Recovers a linear reward via sampling-based Maximum-Entropy IRL.

    Usage::

        trainer = MaxEntIRLTrainer(env=env, demonstrations=demos)
        trainer.train(n_iterations=200)
        reward = trainer.reward_fn()          # LearnedRewardFn
    """

    def __init__(
        self,
        *,
        env,
        demonstrations: Demonstrations,
        save_dir: str = "./policies",
        learning_rate: float = 1e-2,
        background_episodes: int = 64,
        max_steps: int = 200,
        seed: int = 0,
    ) -> None:
        os.makedirs(save_dir, exist_ok=True)
        self._save_dir = save_dir
        self._venv = as_venv(env)
        self._demos = demonstrations
        self._lr = learning_rate
        self._bg_episodes = background_episodes
        self._max_steps = max_steps
        np.random.seed(seed)

        obs_dim = int(np.asarray(demonstrations.obs).shape[1])
        self._reward = LinearRewardNet(obs_dim)

    def train(self, n_iterations: int = 200) -> None:
        torch = _torch()
        opt = torch.optim.Adam(self._reward.module.parameters(), lr=self._lr)

        expert_trajs = [torch.as_tensor(t) for t in _expert_trajectories(self._demos)]
        expert_states = torch.cat(expert_trajs, dim=0)

        log.info(
            "MaxEnt IRL: %d expert trajectories, %d background episodes",
            len(expert_trajs), self._bg_episodes,
        )

        for it in range(n_iterations):
            bg = _rollout_background(self._venv, self._bg_episodes, self._max_steps)
            bg_trajs = [torch.as_tensor(t) for t in bg if len(t) > 0]
            if not bg_trajs:
                continue

            # Expert term: mean per-state reward over expert demonstrations.
            expert_term = self._reward.reward_tensor(expert_states).mean()

            # Background term: importance-weighted by exp(trajectory return).
            returns = torch.stack(
                [self._reward.reward_tensor(t).sum() for t in bg_trajs]
            )
            weights = torch.softmax(returns, dim=0)  # normalised exp(R)/Z
            bg_means = torch.stack(
                [self._reward.reward_tensor(t).mean() for t in bg_trajs]
            )
            background_term = (weights.detach() * bg_means).sum()

            # Maximise expert reward relative to the (reweighted) background.
            loss = -(expert_term - background_term)
            opt.zero_grad()
            loss.backward()
            opt.step()

            if it % 50 == 0:
                log.info("MaxEnt iter %d: loss=%.4f", it, float(loss))
        log.info("MaxEnt IRL training complete")

    def reward_fn(self) -> LearnedRewardFn:
        return LearnedRewardFn(self._reward)

    def save(self, name: str = "maxent_reward") -> str:
        path = os.path.join(self._save_dir, f"{name}.pt")
        return LearnedRewardFn(self._reward).save(path)
