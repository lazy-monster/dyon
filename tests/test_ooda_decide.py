"""Tests for the OODA decision hierarchy (assessment §6.2).

decide() must enforce: hard safety constraints > LLM overseer > RL tactical
control > maintain. We test the precedence directly without building the full
loop (decide only reads self.policy).
"""

from __future__ import annotations

from dyon.autonomous.ooda import OODALoop


def _loop(policy=None):
    loop = OODALoop.__new__(OODALoop)   # bypass heavy __init__
    loop.policy = policy
    return loop


async def test_safety_overrides_overseer():
    loop = _loop(policy=object())
    plan = await loop.decide(
        observation={},
        assessment={"requires_shutdown": True, "reason": "crit",
                    "overseer_action": "increase_rate", "risk_level": "low"},
    )
    assert plan["action"] == "shutdown"


async def test_human_intervention_is_highest_priority():
    loop = _loop()
    plan = await loop.decide({}, {"requires_human_intervention": True,
                                  "requires_shutdown": True})
    assert plan["action"] == "request_human"


async def test_overseer_action_taken_when_no_safety_constraint():
    loop = _loop()
    plan = await loop.decide({}, {"overseer_action": "open_valve",
                                  "overseer_reasoning": "dry", "risk_level": "low"})
    assert plan["action"] == "open_valve"
    assert plan["source"] == "overseer"


async def test_rl_control_only_when_low_risk_and_policy_present():
    loop = _loop(policy=object())
    plan = await loop.decide({}, {"risk_level": "low"})
    assert plan["action"] == "rl_control"


async def test_maintain_when_no_policy():
    loop = _loop(policy=None)
    plan = await loop.decide({}, {"risk_level": "low"})
    assert plan["action"] == "maintain_current"


async def test_overseer_no_action_falls_through_to_rl():
    loop = _loop(policy=object())
    plan = await loop.decide({}, {"overseer_action": "no_action",
                                  "risk_level": "low"})
    assert plan["action"] == "rl_control"
