"""OODALoop: Observe-Orient-Decide-Act autonomous control layer."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from dyon.autonomous.base import AbstractAutonomousController
from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

if TYPE_CHECKING:
    from dyon.autonomous.deployer import PolicyDeployer
    from dyon.autonomous.overseer import AutonomousOverseer
    from dyon.autonomous.planner import GoalPlanner
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore
    from dyon.intelligent.mas import MultiAgentSystem
    from dyon.notifications.notifier import HumanNotifier
    from dyon.reactive.rule_engine import ThresholdRuleEngine
    from dyon.services.ditto.client import DittoClient
    from dyon.simulation.base import TwinModel

log = logging.getLogger(__name__)


# Maps the assessment/overseer risk vocabulary to the three log-event severity
# levels the framework uses (DocumentStore.log_event accepts info/warning/critical).
_RISK_TO_SEVERITY = {
    "low":      "info",
    "medium":   "info",
    "high":     "warning",
    "critical": "critical",
}


class OODALoop(LayerBase, AbstractAutonomousController):
    """
    The OODA loop provides full situational awareness and autonomous control.

    Observe  — gather state from all lower layers
    Orient   — contextualise using models, history, goals
    Decide   — select action plan (rule-based, RL policy, goal planner)
    Act      — execute actions on lower layers or via connectors
    """

    layer_name = "autonomous"

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        cache: CacheStore,
        doc_store: DocumentStore,
        ditto_client: DittoClient,
        models: dict[str, TwinModel],
        reactive: ThresholdRuleEngine,
        mas: MultiAgentSystem,
        connectors: list,
        planner: GoalPlanner,
        policy: PolicyDeployer | None = None,
        loop_interval: int = 5,
        notifier: HumanNotifier | None = None,
        overseer: AutonomousOverseer | None = None,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.doc = doc_store
        self.ditto = ditto_client
        self.models = models
        self.reactive = reactive
        self.mas = mas
        self.connectors = connectors
        self.planner = planner
        self.policy = policy
        self.loop_interval = loop_interval
        self.notifier = notifier
        # Optional LLM overseer — when set, its strategic decision is merged
        # into the assessment dict the rule-based planner produces.
        self.overseer = overseer

    async def observe(self) -> dict:
        base = {
            "state": await self.cache.aget_state(),
            "health": await self.cache.aget_latest_cached("health_score"),
            "telemetry": await self.ts.aget_latest_fields(self.config.field_names),
            "recent_events": await self.doc.aget_recent_events(5),
        }
        if self.mas:
            base["mas_findings"] = {
                a.agent_name: self.mas.get_agent_detail(a.agent_name)
                for a in self.mas.agents
            }
        return base

    async def direct_agent(self, agent_name: str, question: str) -> str:
        """Send a targeted query to a named MAS agent from the autonomous layer."""
        if self.mas is None:
            return "MAS layer not available."
        return await self.mas.ask_agent(agent_name, question)

    async def orient(self, observation: dict) -> dict:
        assessment = await self.planner.assess(observation)
        if self.overseer is not None:
            try:
                decision = await self.overseer.run(observation)
                # The overseer's strategic decision joins the rule-based
                # assessment under explicit keys so safety constraints in
                # decide() can still take precedence over it.
                assessment["overseer_action"] = decision.action
                assessment["overseer_reasoning"] = decision.reasoning
                assessment["overseer_risk_level"] = decision.risk_level
                assessment["overseer_goals_addressed"] = decision.goals_addressed
                assessment["overseer_agent_queries"] = decision.agent_queries
                # Audit trail for every overseer decision.
                try:
                    await self.doc.alog_event(
                        "ooda_overseer_decision",
                        {
                            "action":           decision.action,
                            "reasoning":        decision.reasoning,
                            "risk_level":       decision.risk_level,
                            "goals_addressed":  decision.goals_addressed,
                            "agent_queries":    decision.agent_queries,
                        },
                        severity="info",
                    )
                except Exception as e:
                    self.log.debug("Overseer audit log failed: %s", e)
            except Exception as e:
                self.log.error("Overseer.run failed: %s", e)
        return assessment

    async def decide(self, observation: dict, assessment: dict) -> dict:
        # Layer 1 — hard safety constraints from the rule-based planner.
        # These override the overseer.
        if assessment.get("requires_human_intervention"):
            return {
                "action": "request_human",
                "reason": assessment.get("reason", "intervention required"),
            }
        if assessment.get("requires_shutdown"):
            return {
                "action": "shutdown",
                "reason": assessment.get("reason", "autonomous shutdown"),
            }
        if assessment.get("needs_external_data"):
            return {
                "action": "query_peer",
                "target_twin": assessment.get("target_twin", ""),
                "query": assessment.get("query", {}),
            }

        # Layer 2 — strategic decision from the overseer (if configured).
        # The overseer chooses among the actions declared at construction time.
        overseer_action = assessment.get("overseer_action")
        if overseer_action and overseer_action != "no_action":
            return {
                "action":      overseer_action,
                "reason":      assessment.get("overseer_reasoning", ""),
                "risk_level":  assessment.get("overseer_risk_level", "low"),
                "source":      "overseer",
            }

        # Layer 3 — RL tactical optimisation when conditions are nominal.
        if self.policy and assessment.get("risk_level") == "low":
            return {"action": "rl_control"}
        return {"action": "maintain_current"}

    async def act(self, plan: dict) -> None:
        action = plan["action"]

        if action == "request_human":
            await self.doc.alog_event(
                "human_intervention_requested", plan, severity="critical"
            )
            await self.bus.publish(
                DomainEvent(
                    event_type="autonomous.human_requested",
                    source_layer="autonomous",
                    source_asset=self.config.asset_id,
                    payload=plan,
                    severity="critical",
                )
            )
            self.log.warning("Human intervention requested: %s", plan.get("reason"))
            if self.notifier:
                await self.notifier.send(plan.get("reason", "intervention required"), plan)

        elif action == "shutdown":
            self.reactive.shutdown_asset()  # type: ignore[attr-defined]
            await self.doc.alog_event("autonomous_shutdown", plan, severity="critical")

        elif action == "query_peer":
            for conn in self.connectors:
                if conn.can_reach(plan.get("target_twin", "")):
                    result = await conn.query(plan["target_twin"], plan.get("query", {}))
                    await self.doc.alog_event("peer_query", {"result": result}, severity="info")
                    break

        elif action == "rl_control":
            if self.policy:
                await self.policy.step_once()

        elif plan.get("source") == "overseer":
            # Overseer-defined action: publish an event so domain code can act
            # on it. Subclasses that know the overseer's action vocabulary
            # should override act() to drive their own actuators.
            severity = _RISK_TO_SEVERITY.get(plan.get("risk_level", "low"), "info")
            await self.bus.publish(
                DomainEvent(
                    event_type=f"autonomous.{action}",
                    source_layer="autonomous",
                    source_asset=self.config.asset_id,
                    payload=plan,
                    severity=severity,
                )
            )
            await self.doc.alog_event(
                f"autonomous_{action}",
                plan,
                severity=severity,
            )

    async def start(self) -> None:
        self._running = True
        self.log.info("OODA autonomous loop started (interval=%ds)", self.loop_interval)
        while self._running:
            try:
                obs = await self.observe()
                assessment = await self.orient(obs)
                plan = await self.decide(obs, assessment)
                await self.act(plan)
                # Publish a compact summary for the dashboard and monitoring tools.
                # Suppressed so a Redis outage cannot kill the control loop.
                with contextlib.suppress(Exception):
                    await self.cache.aset_latest("ooda_last_cycle", {
                        "action":     plan.get("action", "unknown"),
                        "reason":     plan.get("reason", ""),
                        "risk_level": assessment.get("risk_level", ""),
                        "ts_s":       int(time.time()),
                    })
            except Exception as e:
                self.log.error("OODA loop error: %s", e)
            await asyncio.sleep(self.loop_interval)
