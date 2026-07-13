# 09 — The Agent Layer: Governance

The control tier responds to readings; the reasoning tier explains them. Neither
decides, at a strategic level, what the asset should *do* — keep running, call for
a human, ask another twin for data, or take control action itself. That is this
tier's job, and it is the top of a single twin.

It works as an **OODA loop** — observe, orient, decide, act — running every few
seconds. Each pass gathers the situation, makes sense of it, chooses an action,
and carries it out. The four steps map directly onto four methods you can see and
override, and the whole loop is built so that the riskier a decision is, the more
control sits with deterministic safety rules rather than with the AI.

---

## The loop, step by step

**Observe** takes a snapshot from the layers below — the FSM state, the health
score, the latest reading of every field, the most recent events, and the current
findings of each MAS agent. It only gathers; it draws no conclusions.

**Orient** makes sense of that snapshot. At minimum a `GoalPlanner` reads it and
returns a structured risk assessment. If you wire in an `AutonomousOverseer`
(below), an LLM does this step instead, reading the agent findings and producing a
reasoned decision. Either way, orient is where "temperature is 72 °C" becomes
"this asset is at high risk and needs attention."

**Decide** turns the assessment into a concrete plan, through a strict three-level
hierarchy that we will return to, because it is the heart of the design.

**Act** executes the plan — escalating to a human, shutting the asset down,
querying a peer twin, or running one step of an RL policy — and caches a one-line
summary of the cycle to Redis under `ooda_last_cycle` so a dashboard can show what
the twin just did.

---

## Orient with rules: the GoalPlanner

The `GoalPlanner` is the simplest way to do the orient step. It is not an LLM and
not a learner — it is a plain rule-based assessor. You give it goals, which are
declarations of intent, and it reads the observation and reports whether the
situation threatens them:

```python
from dyon.autonomous import GoalPlanner, Goal

planner = GoalPlanner(goals=[
    Goal(name="prevent_shutdown", description="Avoid the shutdown state", priority=20),
    Goal(name="maintain_health", description="Keep health above 80", priority=10),
])
```

Its `assess()` reads the health score, the state, and the count of recent critical
events, and returns a risk level plus a few flags the decide step acts on:

| Situation                          | risk_level | flag set                       |
|------------------------------------|------------|--------------------------------|
| running, health ≥ 75               | low        | —                              |
| running, health 50–75              | medium     | —                              |
| warning, or health < 50            | high       | requires intervention          |
| shutdown                           | critical   | requires human intervention    |
| three or more recent critical events | critical | requires human intervention    |

The planner notices threats; it does not optimise toward the goals. Working out
the *best* action to achieve them is what an RL policy does, later in this chapter.
To tailor the assessment to your asset, subclass it and extend `assess()`:

```python
class PumpGoalPlanner(GoalPlanner):
    async def assess(self, observation: dict) -> dict:
        result = await super().assess(observation)
        t = observation.get("telemetry", {})
        temp, pressure = t.get("temperature_c"), t.get("pressure_bar")
        if temp and pressure and temp > 65.0 and pressure < 3.8:
            result["risk_level"] = "critical"
            result["requires_human_intervention"] = True
            result["reason"] = "combined thermal and pressure degradation"
        return result
```

---

## Decide: the safety hierarchy

`decide()` is deliberately layered so that the AI never overrules safety. It works
through three levels in order and stops at the first that produces an action.

The first level is **hard safety constraints** from the assessment. If it flags
that a human is required, the plan is `request_human` and nothing else is
consulted. The same is true for a required shutdown or a need for external data.
These come from deterministic rules and cannot be talked out of.

The second level is the **overseer's strategic decision**, if you wired one in. As
long as no safety constraint fired, an overseer action other than `no_action`
becomes the plan.

The third level is **RL tactical control**. With no constraint and no overseer
action, if a policy is loaded *and* the risk is `low`, the plan is `rl_control` —
the strategic question is already settled, so the policy simply optimises the
output. Otherwise the cycle ends in `maintain_current`, and nothing happens.

The shape is intentional: safety rules first, strategic reasoning second, learned
control last. The RL policy can never take an action the safety level would forbid,
and it is never asked to reason about strategy it was not trained for.

Each plan maps to a concrete effect in `act()`:

| Plan               | Produced when                                   | Effect |
|--------------------|-------------------------------------------------|--------|
| `request_human`    | the assessment requires human intervention      | logs a critical event, publishes `autonomous.human_requested`, and calls the notifier if one is wired in |
| `shutdown`         | the assessment requires a shutdown              | calls `reactive.shutdown_asset()` and logs it |
| `query_peer`       | the assessment needs external data              | uses a connector to query the named peer twin |
| `<overseer action>`| the overseer chose a non-`no_action` action     | publishes `autonomous.<action>` and logs it, for domain code to act on |
| `rl_control`       | a policy is loaded and risk is `low`            | runs one inference step of the policy |
| `maintain_current` | none of the above                               | nothing |

---

## Wiring the loop together

The OODA loop needs references to the lower layers it observes and acts on, so you
build those first inside `build_layers()`, keep the references, and pass them in:

```python
from dyon.autonomous import OODALoop, GoalPlanner, Goal

def build_layers(self):
    ts, doc, cache = InfluxAdapter(self.config), MongoAdapter(self.config), RedisAdapter(self.config)
    ditto = DittoClient(self.config)

    router   = TelemetryRouter(self.config, self.bus, ts_store=ts, doc_store=doc, cache=cache)
    reactive = ThresholdRuleEngine(self.config, self.bus, ts_store=ts, cache=cache, doc_store=doc)
    mas      = MultiAgentSystem(self.config, self.bus, agents=[...], cache=cache, doc_store=doc)
    planner  = GoalPlanner([Goal(name="maintain_health", description="...", priority=10)])

    ooda = OODALoop(self.config, self.bus,
                    ts_store=ts, cache=cache, doc_store=doc, ditto_client=ditto,
                    models={}, reactive=reactive, mas=mas, connectors=[],
                    planner=planner,
                    policy=None,       # optional PolicyDeployer
                    overseer=None,     # optional AutonomousOverseer
                    loop_interval=5)

    return {"data": router, "network": MQTTIngestor(self.config, self.bus, router=router),
            "reactive": reactive, "intelligent": mas, "autonomous": ooda}
```

With just a planner, this gives you a twin that escalates to a human when health
drops or it shuts down, and otherwise holds steady. The next two sections add the
two optional brains: an LLM overseer for strategy, and an RL policy for control.

---

## Orient with an LLM: the AutonomousOverseer

When a rule-based assessment is too blunt, an `AutonomousOverseer` does the orient
step with a language model. It reads every MAS agent's findings, can interrogate
specific agents for more detail, and returns a structured decision with its full
reasoning recorded for audit. Because it has the whole picture — and, in a
multi-twin system, can reach across twins — it can act on patterns no single agent
sees.

You give it the LLM, the twin's MAS, the goals, and the vocabulary of actions it
may choose:

```python
from dyon.autonomous.overseer import AutonomousOverseer
from dyon.intelligent.agent import build_llm

overseer = AutonomousOverseer(
    config=config, llm=build_llm(config), mas=mas,
    goals=["maintain_health", "prevent_shutdown"],
    available_actions=["reduce_load", "schedule_maintenance", "no_action"],
)

ooda = OODALoop(..., overseer=overseer)
```

Each cycle, the overseer formats the observation and the agent findings into a
prompt, optionally calls its `query_mas_agent` tool for deeper analysis, and must
call its `submit_decision` tool to return an `OverseerDecision` — an `action`, the
`reasoning` behind it, a `risk_level`, the goals it addresses, and the agent
queries it made along the way. If it errors or never submits, it returns a safe
`no_action` default, so the loop always has a valid decision and the safety level in
`decide()` still governs.

`available_actions` is a hard vocabulary, not a suggestion. If the model submits
an action outside the list — a hallucinated verb, a rephrasing, a typo — the
decision is rejected and the rejection is fed back to the model, which gets
another attempt within its iteration budget; a model that never produces a valid
action falls through to the same `no_action` default. So the only strings that
can ever leave the overseer as `autonomous.<action>` events are ones you wrote
yourself, which is what makes it safe for `act()` to dispatch on them. The
prompt is defended in the other direction too: sensor values, event text, and
anything else that originates outside the twin is fenced off as data in the
prompt, so a malicious string arriving through telemetry cannot restate the
overseer's instructions.

Every overseer decision is logged to MongoDB as `ooda_overseer_decision` during
the orient step — before any safety gating — so the audit trail records what the
overseer actually recommended even when a constraint overrides it. For a
multi-twin system, pass an `extra_query_fn` so the overseer can question agents in
other twins:

```python
async def cross_twin_query(agent_name: str, question: str) -> str:
    for other_mas in (pump_b.mas, manifold.mas):
        for agent in other_mas.agents:
            if agent.agent_name == agent_name:
                return await other_mas.ask_agent(agent_name, question)
    return f"Agent '{agent_name}' not found."

overseer = AutonomousOverseer(config, build_llm(config), mas,
                              goals=[...], available_actions=[...],
                              extra_query_fn=cross_twin_query)
```

When the overseer's chosen action reaches the act step, the loop publishes it as an
`autonomous.<action>` event and logs it; to actually drive an actuator, override
`act()` in your OODA subclass to handle your action vocabulary.

---

## Control with a learned policy: RL

The third level of `decide()` runs a reinforcement-learning policy — a small neural
network that maps the current readings to a control output. Unlike the PID
controller of chapter 07, which follows a fixed formula, an RL policy *learns* its
strategy by trial and error against a simulated model, which lets it handle
non-linear dynamics a hand-tuned formula struggles with. The trade-off is that it
must be trained first and is not interpretable.

Training happens against a Gymnasium environment that wraps one of your simulation
models. You declare what the agent sees (`obs_fields`), the single output it
adjusts (`control_field`), and the value it is trying to hold at a target:

```python
import numpy as np
from dyon.autonomous import GenericTwinEnv, PolicyTrainer

env = GenericTwinEnv(
    model=physics_model,              # a TwinModel from chapter 05
    control_field="speed_rpm",        # the output the agent sets
    process_variable="pressure_bar",  # the value it regulates
    target=4.2,                       # the value it aims for
    ctrl_min=800.0, ctrl_max=2200.0,  # the physical range of the output
    obs_fields=["temperature_c", "pressure_bar", "flow_rate_lpm"],
    obs_low=np.array([0.0, 0.0, 0.0]),
    obs_high=np.array([100.0, 10.0, 300.0]),
    max_steps=500,
)
```

Each step, the agent sees the observation vector, outputs an action that is scaled
into the control range, the model advances, and the agent receives a reward. By
default the reward is `-abs(process_variable - target)` — zero when it hits the
target, increasingly negative the further off it is — so over many episodes the
learner shapes the network toward actions that keep the value on target.

You train with `PolicyTrainer`, choosing one of four algorithms (SAC is the
default and a good choice for continuous control; TD3, PPO, and A2C are also
available):

```python
trainer = PolicyTrainer(env, algorithm="SAC", save_dir="./policies")
trainer.train(total_timesteps=200_000)
trainer.save("pump_pressure_policy")     # → ./policies/pump_pressure_policy.zip
```

The CLI wraps the same flow: `dyon train --timesteps 200000 --algorithm SAC
--save pump_pressure_policy`, loading the environment from a module that exposes an
`env` object.

To run the trained policy live, hand a `PolicyDeployer` to the OODA loop. When the
decide step returns `rl_control`, the deployer reads the observation fields from
InfluxDB, runs one deterministic forward pass, scales the result, and publishes the
control command to MQTT:

```python
from dyon.autonomous import PolicyDeployer

policy = PolicyDeployer(
    policy_path="./policies/pump_pressure_policy.zip",
    config=self.config, ts_store=ts, mqtt_transport=mqtt,
    obs_fields=["temperature_c", "pressure_bar", "flow_rate_lpm"],
    control_field="speed_rpm", ctrl_min=800.0, ctrl_max=2200.0,
    algorithm="SAC",
)
ooda = OODALoop(..., policy=policy)
```

If you need more than distance-to-target, pass your own `reward_fn(obs, target)`
when building the environment — for example, tracking the target while penalising
high temperature:

```python
def shaped_reward(obs: dict, target: float) -> float:
    pressure = obs.get("sim_pressure_bar", 0.0)
    temp     = obs.get("sim_temperature_c", 100.0)
    return -abs(pressure - target) - max(0.0, temp - 60.0) * 0.1

env = GenericTwinEnv(..., reward_fn=shaped_reward)
```

`PolicyDeployer` can also run a policy you already hold in memory rather than load
from a file — pass it as `policy=...` instead of a path. That is the hook the next
section depends on.

---

## When you cannot write the reward

Everything above assumes you can express what "good" means as a reward function.
Often you cannot — when the behaviour you want is a human skill that is easier to
demonstrate than to score. For those cases the framework includes a whole toolkit,
`dyon.learning`, that learns from demonstrations instead: it can clone an
expert's actions directly, or recover the reward function implicit in their
behaviour and feed it back into the same `GenericTwinEnv` and `PolicyDeployer` you
have just seen. That is the subject of chapter 12.

---

## Escalating to humans

When the loop decides `request_human`, it publishes an `autonomous.human_requested`
event and, if you gave it a `HumanNotifier`, sends a message through every backend
the notifier holds:

```python
from dyon.notifications.notifier import HumanNotifier, SlackBackend, EmailBackend

notifier = HumanNotifier(backends=[
    SlackBackend(webhook_url=os.getenv("SLACK_WEBHOOK")),
    EmailBackend(smtp_host="smtp.example.com", smtp_port=587,
                 sender="dyon@example.com", recipients=["oncall@example.com"],
                 username=os.getenv("EMAIL_USER"), password=os.getenv("EMAIL_PASS")),
])

ooda = OODALoop(..., notifier=notifier)
```

The framework ships `SlackBackend`, `EmailBackend`, and `WebhookBackend`, and a
notifier sends to all of them at once; pass an empty list to stay silent (useful in
tests). For anything more bespoke, subscribe to `autonomous.human_requested` on the
bus and handle it yourself.

---

That completes a single self-managing twin: it senses, models, exposes, reacts,
diagnoses, and decides. The next chapter steps outside one twin — to how twins talk
to each other, and how many of them combine into one.
