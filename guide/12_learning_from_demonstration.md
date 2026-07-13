# 12 — Learning from Demonstration

Chapter 09 trained an RL policy against a reward you wrote by hand. That works
when "good behaviour" is something you can score with a formula. Often it is not.
The behaviour you want may be a human skill — an operator nursing a temperamental
machine, a pilot judging a crosswind landing — that an expert can *demonstrate*
far more easily than anyone can *describe*. This chapter is about learning from those
demonstrations, using the `dyon.learning` package.

A policy, recall, is just a function from state to action. There are three ways to
obtain one, and they differ in what they need:

- **Reinforcement learning** needs a reward function and discovers a policy by
  trial and error. Use it when you can write the reward.
- **Imitation learning** needs demonstrations and produces a policy that copies
  the expert. Use it when you have expert data and simply want to reproduce it.
- **Inverse RL** needs demonstrations *and* an environment, and recovers the
  reward function the expert seems to be following — and, with it, a policy. Use it
  when the reward is the hard part.

The insight behind the third option is that for nuanced skills, the reward is the
thing you cannot write down. Inverse RL recovers it from behaviour instead of
asking you to guess. Everything in this chapter is domain-agnostic: the same tools
that teach a robot arm a trajectory teach a dispatcher when to reroute a fleet.

> The inverse-RL and adversarial trainers wrap the
> [`imitation`](https://imitation.readthedocs.io) library, which pins
> `gymnasium 0.29.x` and `stable-baselines3 2.2.x`. Those bounds are set in
> `pyproject.toml`; upgrading past them breaks `imitation`.

---

## Describing the observation: FeatureSpec

Whatever you learn is only as good as the features it sees, and a subtle bug class
in this kind of work is the observation vector and its declared space drifting out
of step. `FeatureSpec` removes that risk by defining the observation once and
deriving both the numeric vector and the matching Gymnasium `Box` from the same
column list:

```python
from dyon.learning import FeatureSpec, Scalar, Categorical

spec = FeatureSpec(version=1, columns=[
    Scalar("temperature", lambda c: c["temperature"], 0.0, 100.0),
    Scalar("pressure",    lambda c: c["pressure"],    0.0, 10.0),
    Categorical("mode", lambda c: c["mode"], ["idle", "run", "fault"], one_hot=True),
])

box = spec.box()                                # Box of shape (len(spec),)
vec = spec.encode({"temperature": 55.0, "pressure": 4.2, "mode": "run"})
```

Each column's extractor reads from whatever context you pass to `encode` — a dict,
a session object, anything. `Scalar` clips to its bounds so a stray reading cannot
violate the space, and `Categorical` encodes one-hot or as a normalised index. The
spec carries a `version`; tag every recorded demonstration with it, so a trainer
can refuse data captured under a different layout. A long-lived twin leans on this
heavily: its observations are versioned precisely so that recordings made before a
sensor was added are never mixed with those made after.

---

## The common data format: Demonstrations

Every algorithm here consumes the same bundle — arrays of transitions plus the
spaces they were recorded in:

```python
from dyon.learning import Demonstrations

demos = Demonstrations(
    obs=obs_array,            # (N, obs_dim)
    acts=act_array,           # (N,) discrete, or (N, act_dim) continuous
    next_obs=next_obs_array,  # (N, obs_dim)
    dones=done_array,         # (N,) bool — episode-final flags
    observation_space=env.observation_space,
    action_space=env.action_space,
    episode_ids=ep_array,     # optional — lets it regroup into trajectories
)
```

You feed demonstrations in through a `DemonstrationSource`. The framework provides
`ArrayDemonstrationSource` for arrays you already hold and
`CorpusDemonstrationSource` for a versioned `.npz` in a `TrainingCorpus`. Where
your demonstrations live somewhere else — a twin that has been logging its own
transitions to MongoDB, say — subclass `DemonstrationSource` and load them there,
filtering by feature version so a stale layout can never slip in. When you need a
held-out set for validation, `split_demonstrations(demos, holdout_frac=0.2)`
provides one.

---

## Copying the expert: behavioural cloning

The simplest method is behavioural cloning — plain supervised learning that maps
each demonstrated state to the action the expert took. It needs no environment,
only the demonstrations and their spaces. The trained policy is hosted inside an
SB3 `PPO` container so it saves and loads exactly like the RL and IRL policies, and
deploys through the same `PolicyDeployer`:

```python
from dyon.learning import BCTrainer

bc = BCTrainer(demonstrations=demos, save_dir="./policies")
bc.train(n_epochs=20)
bc.save("cloned_policy")        # → ./policies/cloned_policy.zip
model = bc.get_sb3_model()      # use this to warm-start an IRL generator
```

Cloning is fast and makes a strong baseline, but it has a known weakness:
*compounding error*. In a state no expert ever visited, the policy has no signal
and can drift further and further off course. When you have an interactive expert —
an oracle or simulator that can label new states on demand — `DAggerTrainer` fixes
this by interleaving the learner's own rollouts with fresh expert labels for the
states it actually reaches.

---

## Recovering the reward: AIRL

The headline method is adversarial inverse RL. `AIRLTrainer` trains a generator
policy against a discriminator that learns to tell expert transitions from
generated ones, using the environment only for its dynamics — the reward itself is
learned. When training finishes you get two artifacts: a policy *and* a reusable
reward network.

```python
from dyon.learning import AIRLTrainer

airl = AIRLTrainer(env=env, demonstrations=demos,
                   init_policy=bc.policy,         # warm-start from BC: faster, steadier
                   allow_variable_horizon=True)   # for variable-length episodes
airl.train(total_timesteps=100_000)

reward = airl.reward_fn()         # the recovered reward, as a LearnedRewardFn
paths  = airl.save("airl_policy") # writes the policy .zip and the reward .pt
```

`GAILTrainer` shares the same interface but its discriminator is *not* a reusable
reward — reach for it when you want the strongest imitation and do not need the
reward back. `MaxEntIRLTrainer` is a classic, dependency-free alternative that
recovers a simpler linear reward.

The recovered reward is where inverse RL earns its keep, because it transfers even
when the policy cannot. `LearnedRewardFn` makes it behave like any other reward —
you can optimise a fresh policy against it, even under new dynamics, with no new
demonstrations:

```python
from dyon.learning import LearnedRewardFn
from stable_baselines3 import PPO

reward = LearnedRewardFn.load("./policies/airl_policy_reward.pt")

wrapped = reward.wrap_venv(env)                  # overlay it on a vectorised env
model = PPO("MlpPolicy", wrapped).learn(50_000)  # train a new policy on it

# or drop it straight into the framework's generic RL env from chapter 09:
env = GenericTwinEnv(..., reward_fn=reward.make_generic_reward_fn(obs_fields))
```

---

## The whole flow at once: the skill-transfer pipeline

These stages compose into a single repeatable operation, and
`SkillTransferPipeline` chains them: behavioural cloning to warm-start, AIRL to
recover the reward and refine, then ordinary RL to optimise that learned reward.

```python
from dyon.learning import SkillTransferPipeline

pipe = SkillTransferPipeline(env=env, demonstration_source=my_source,
                             save_dir="./policies",
                             corpus=training_corpus,   # optional: versions results
                             dataset_name="my_skill")

result = pipe.run(bc_epochs=20, airl_timesteps=100_000,
                  final_rl_timesteps=50_000,           # 0 to stop after AIRL
                  allow_variable_horizon=True)
# result.model, result.reward, result.stages_run
```

Every stage is optional, which means the same object also handles reward *reuse*:
point it at a saved reward and run only the final RL stage to retrain under new
conditions.

```python
pipe.run(bc_epochs=0, airl_timesteps=0,
         reward_path="./policies/my_skill_reward.pt",
         final_rl_timesteps=50_000, final_rl_from_scratch=True)
```

Crucially, the pipeline does not deploy blindly. Score a candidate on held-out
demonstrations first, and promote it only if it beats the incumbent:

```python
train, holdout = split_demonstrations(my_source.load(), 0.2)
result = pipe.run(demos=train, bc_epochs=20, airl_timesteps=100_000)
result.metrics = pipe.evaluate(result.model, holdout, result.reward)
# {"action_match": 0.81, "mean_return": 23.4}

if pipe.should_promote(result.metrics, current_metrics):
    pipe.version(result)         # push the policy and reward to the corpus
    deploy(result.model)
```

Versioning through the corpus buys integrity as well as bookkeeping. Policy and
reward files are pickle-format under the hood (SB3 zips, torch checkpoints), and
unpickling a file *executes* code inside it — so a twin must never load one it
cannot trust. The corpus closes that loop: every `push_version` records a
SHA-256 checksum of the artifact in the dataset's manifest, every download
verifies the bytes against it, and a mismatch deletes the file and raises
`IntegrityError` before anything deserializes it. What a twin loads is therefore
byte-identical to what your trusted trainer uploaded, even if the object store
sits on shared infrastructure.

`action_match` is the fraction of held-out expert actions the policy reproduces
(for discrete actions); `mean_return` is the average return under the learned
reward. Finally, `SyncTrigger` decides *when* to re-run all of this — when enough
new demonstrations have accrued, or an interval has passed — so a twin can keep
improving from fresh expert data on its own:

```python
from dyon.learning import SyncTrigger

trigger = SyncTrigger(demo_threshold=100, interval_seconds=86_400)
fire, reason = trigger.should_fire(current_demo_count)
if fire:
    run_pipeline_and_maybe_promote()
    trigger.mark_fired(current_demo_count)
```

Run that check from a background task on your twin — every few hours, or whenever
a batch of demonstrations lands — and the circle closes on its own, from expert
demonstration to retrained policy to deployed skill.

---

## Deploying a learned policy

Because every learner here saves an SB3 `.zip`, a learned policy deploys through
the same `PolicyDeployer` from chapter 09. Pass `algorithm="BC"` so it loads
through the PPO container, or hand it a policy object you already hold:

```python
from dyon.autonomous import PolicyDeployer

policy = PolicyDeployer(policy_path="./policies/cloned_policy.zip",
                        config=self.config, ts_store=ts, mqtt_transport=mqtt,
                        obs_fields=[...], control_field="...",
                        ctrl_min=..., ctrl_max=..., algorithm="BC")
ooda = OODALoop(..., policy=policy)
```

`PolicyDeployer` suits twins that control a continuous variable. For twins whose
"action" is a discrete, context-driven choice — a dispatcher choosing a route, for
instance — you wrap the trained model in a small adapter that builds the
observation from your own state and maps the action index back to a domain action.
The adapter is the only domain-specific part; the trained policy underneath is the
same object either way.

---

## Which method to reach for

- You can write a good reward → plain RL (chapter 09).
- You have expert data and want to copy it quickly → `BCTrainer`.
- Cloning drifts and you have an oracle → `DAggerTrainer`.
- You want the strongest imitation and don't need the reward → `GAILTrainer`.
- The reward is the hard part, or you want to reuse it → `AIRLTrainer`.
- You want the full, validated, self-improving loop → `SkillTransferPipeline` with
  `SyncTrigger`.

That covers everything the twin can *do*. The final chapter turns to how a person
*sees* it: a live dashboard, built from the same config, that puts a face on all
of it.
