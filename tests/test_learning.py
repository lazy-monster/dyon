"""
Domain-agnostic tests for dyon.learning.

Proves the Learning-from-Demonstration toolkit works with NO domain-specific
imports — on plain Gymnasium envs and synthetic data — so it is genuinely
reusable by any twin.
"""

from __future__ import annotations

import gymnasium as gym
import numpy as np
import pytest
from gymnasium import spaces

from dyon.learning import (
    ArrayDemonstrationSource,
    BCTrainer,
    Categorical,
    Demonstrations,
    FeatureSpec,
    LearnedRewardFn,
    MaxEntIRLTrainer,
    Scalar,
    SkillTransferPipeline,
    SyncTrigger,
    action_match_accuracy,
    split_demonstrations,
)


# --------------------------------------------------------------------------- #
# FeatureSpec
# --------------------------------------------------------------------------- #
def test_feature_spec_vector_matches_box_and_names():
    spec = FeatureSpec(
        version=2,
        columns=[
            Scalar("a", lambda c: c["a"], 0.0, 1.0),
            Categorical("cat", lambda c: c["cat"], ["x", "y", "z"], one_hot=True),
            Categorical("idx", lambda c: c["idx"], ["p", "q"], one_hot=False),
        ],
    )
    assert spec.version == 2
    # 1 scalar + 3 one-hot + 1 index = 5 columns
    assert len(spec) == 5
    assert len(spec.names) == 5
    box = spec.box()
    assert box.shape == (5,)
    vec = spec.encode({"a": 0.5, "cat": "y", "idx": "q"})
    assert vec.shape == (5,)
    assert box.contains(vec)
    # one-hot for "y"
    assert list(vec[1:4]) == [0.0, 1.0, 0.0]
    # index normalised: q is index 1 of 2 → 1.0
    assert vec[4] == pytest.approx(1.0)


def test_feature_spec_clips_and_handles_unknown_category():
    spec = FeatureSpec(version=1, columns=[
        Scalar("a", lambda c: c["a"], 0.0, 1.0),
        Categorical("cat", lambda c: c["cat"], ["x", "y"], one_hot=True),
    ])
    vec = spec.encode({"a": 9.9, "cat": "unknown"})
    assert vec[0] == 1.0                      # clipped to high
    assert list(vec[1:3]) == [0.0, 0.0]       # unknown → all-zero one-hot


# --------------------------------------------------------------------------- #
# Demonstrations
# --------------------------------------------------------------------------- #
def _toy_demos(n=20, obs_dim=3):
    obs = np.random.rand(n, obs_dim).astype(np.float32)
    return Demonstrations(
        obs=obs,
        acts=np.random.randint(0, 2, size=n),
        next_obs=np.random.rand(n, obs_dim).astype(np.float32),
        dones=np.zeros(n, dtype=bool),
        observation_space=spaces.Box(0, 1, shape=(obs_dim,), dtype=np.float32),
        action_space=spaces.Discrete(2),
        episode_ids=np.repeat(np.arange(n // 5), 5),
    )


def test_demonstrations_length_validation():
    with pytest.raises(ValueError):
        Demonstrations(
            obs=np.zeros((3, 2)), acts=np.zeros(2), next_obs=np.zeros((3, 2)),
            dones=np.zeros(3), observation_space=spaces.Box(0, 1, (2,)),
            action_space=spaces.Discrete(2),
        )


def test_demonstrations_to_transitions_and_trajectories():
    d = _toy_demos()
    t = d.to_imitation_transitions()
    assert len(t.obs) == len(d)
    trajs = d.to_trajectories()
    assert len(trajs) == 4               # 20 transitions / 5 per episode
    # Trajectory holds T+1 observations for T actions.
    assert len(trajs[0].obs) == len(trajs[0].acts) + 1


def test_split_demonstrations():
    d = _toy_demos(n=20)
    train, hold = split_demonstrations(d, holdout_frac=0.25, seed=1)
    assert len(hold) == 5
    assert len(train) == 15


# --------------------------------------------------------------------------- #
# Promotion / trigger logic (no training needed)
# --------------------------------------------------------------------------- #
def test_should_promote_logic():
    sp = SkillTransferPipeline.should_promote
    assert sp({"action_match": 0.8}, None) is True          # no current → promote
    assert sp({"action_match": 0.9}, {"action_match": 0.8}) is True
    assert sp({"action_match": 0.7}, {"action_match": 0.8}) is False
    # tie on action_match → decide on return
    assert sp({"action_match": 0.8, "mean_return": 5},
              {"action_match": 0.8, "mean_return": 4}) is True


def test_sync_trigger():
    trig = SyncTrigger(demo_threshold=10, interval_seconds=10_000)
    fire, why = trig.should_fire(5)
    assert fire is False
    fire, why = trig.should_fire(10)
    assert fire is True and why == "demo_threshold"
    trig.mark_fired(10)
    assert trig.should_fire(15)[0] is False


# --------------------------------------------------------------------------- #
# Learned reward round-trip (via MaxEnt's linear reward net)
# --------------------------------------------------------------------------- #
def test_learned_reward_save_load(tmp_path):
    env = gym.make("CartPole-v1")
    demos = _cartpole_random_demos(env, n=60)
    me = MaxEntIRLTrainer(env=gym.make("CartPole-v1"), demonstrations=demos,
                          save_dir=str(tmp_path), background_episodes=4, max_steps=50)
    me.train(n_iterations=3)
    path = me.save("rwd")
    loaded = LearnedRewardFn.load(path)
    r = loaded(demos.obs[:4])
    assert np.asarray(r).reshape(-1).shape == (4,)


# --------------------------------------------------------------------------- #
# Integration: BC + AIRL + pipeline on CartPole (tiny budgets)
# --------------------------------------------------------------------------- #
def _cartpole_random_demos(env, n=200):
    obs_l, act_l, nobs_l, done_l, ep = [], [], [], [], []
    obs, _ = env.reset()
    e = 0
    for _ in range(n):
        a = env.action_space.sample()
        nobs, _r, term, trunc, _ = env.step(a)
        obs_l.append(obs)
        act_l.append(a)
        nobs_l.append(nobs)
        done_l.append(term or trunc)
        ep.append(e)
        obs = nobs
        if term or trunc:
            obs, _ = env.reset()
            e += 1
    return Demonstrations(
        obs=np.array(obs_l, dtype=np.float32), acts=np.array(act_l),
        next_obs=np.array(nobs_l, dtype=np.float32),
        dones=np.array(done_l, dtype=bool),
        observation_space=env.observation_space, action_space=env.action_space,
        episode_ids=np.array(ep),
    )


@pytest.mark.slow
def test_bc_trains_and_predicts(tmp_path):
    env = gym.make("CartPole-v1")
    demos = _cartpole_random_demos(env, 200)
    bc = BCTrainer(demonstrations=demos, save_dir=str(tmp_path))
    bc.train(n_epochs=2)
    acc = action_match_accuracy(bc.get_sb3_model(), demos)
    assert 0.0 <= acc <= 1.0
    p = bc.save("bc")
    assert p.endswith(".zip")


@pytest.mark.slow
def test_pipeline_bc_airl_and_reward_reuse(tmp_path):
    src = ArrayDemonstrationSource(
        **_demos_as_kwargs(_cartpole_random_demos(gym.make("CartPole-v1"), 200))
    )
    pipe = SkillTransferPipeline(
        env=gym.make("CartPole-v1"), demonstration_source=src,
        save_dir=str(tmp_path), dataset_name="t",
    )
    res = pipe.run(bc_epochs=1, airl_timesteps=2048, final_rl_timesteps=0,
                   allow_variable_horizon=True)
    assert "bc" in res.stages_run and "airl" in res.stages_run
    assert res.reward is not None
    # Reward reuse from scratch.
    rp = res.reward.save(str(tmp_path / "r.pt"))
    res2 = pipe.run(bc_epochs=0, airl_timesteps=0, reward_path=rp,
                    final_rl_timesteps=2048, final_rl_from_scratch=True)
    assert res2.stages_run == ["rl_scratch"]


def _demos_as_kwargs(d: Demonstrations) -> dict:
    return dict(
        obs=d.obs, acts=d.acts, next_obs=d.next_obs, dones=d.dones,
        observation_space=d.observation_space, action_space=d.action_space,
        episode_ids=d.episode_ids,
    )
