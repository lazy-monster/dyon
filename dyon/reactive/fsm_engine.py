"""MultiStateFSMRuleEngine — domain-agnostic configurable N-state FSM rule engine.

This is a framework-level, domain-agnostic extension to ThresholdRuleEngine.
Subclasses define custom states, transitions, and state-selection logic.
Nothing in this file is domain-specific.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from transitions import Machine

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent
from dyon.reactive.base import Rule

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore

log = logging.getLogger(__name__)


class MultiStateFSMRuleEngine(LayerBase):
    """
    Domain-agnostic rule engine backed by a configurable finite state machine.

    Subclasses define the domain-specific FSM by overriding class attributes
    and the ``compute_desired_state`` method.

    Class attributes to override:
        _states       : list of state name strings (e.g. ["NOMINAL", "ALERT", "FAULT"])
        _transitions  : list of transition dicts with keys trigger, source, dest
        _initial_state: string name of the starting state
        _severity_map : dict mapping state names to severity strings
                        ("info" | "warning" | "critical")

    Abstract method to implement:
        compute_desired_state(readings) -> str | None
            Given current sensor readings, return the desired next state string,
            or None to keep the current state unchanged.

    The engine adds ``_goto_<state>`` triggers for each state automatically,
    allowing direct transitions regardless of current state. Subclass transitions
    are also respected and take precedence.
    """

    layer_name = "reactive"

    _states: list[str] = ["idle", "active", "fault"]
    _transitions: list[dict] = [
        {"trigger": "activate",       "source": "idle",            "dest": "active"},
        {"trigger": "deactivate",     "source": "active",          "dest": "idle"},
        {"trigger": "fault_detected", "source": ["idle", "active"],"dest": "fault"},
        {"trigger": "recover",        "source": "fault",           "dest": "idle"},
    ]
    _initial_state: str = "idle"
    _severity_map: dict[str, str] = {"fault": "critical"}

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        cache: CacheStore,
        doc_store: DocumentStore,
        custom_rules: list[Rule] | None = None,
        eval_interval: int = 15,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.doc = doc_store
        self.custom_rules = custom_rules or []
        self.eval_interval = eval_interval

        # Build complete transitions list: subclass transitions + _goto_* for each state
        goto_transitions = [
            {"trigger": f"_goto_{s}", "source": "*", "dest": s}
            for s in self._states
        ]
        all_transitions = list(self._transitions) + goto_transitions

        # ignore_invalid_triggers prevents the library from raising MachineError
        # when compute_desired_state() returns a state that is unreachable from
        # the current state (e.g. WILTING_RISK when current is OPTIMAL). The
        # subclass is responsible for enforcing valid transition paths.
        Machine(
            model=self,
            states=self._states,
            initial=self._initial_state,
            transitions=all_transitions,
            ignore_invalid_triggers=True,
        )

    def compute_desired_state(self, readings: dict[str, float | None]) -> str | None:
        """
        Compute the desired next FSM state based on current sensor readings.

        Called every evaluation cycle. Return a state name string to request
        a transition, or None to stay in the current state.

        The ``readings`` dict includes all sensor fields from the config plus
        any synthetic values set by custom rules (keys prefixed with ``_rule_``).

        Subclasses should implement this with domain-specific logic.
        """
        return None

    async def evaluate(self) -> str:
        """Run custom rules, compute desired state, execute transition, return state."""
        # One batched query for every field, off the event loop.
        readings: dict[str, float | None] = await self.ts.aget_latest_fields(
            self.config.field_names
        )

        for rule in self.custom_rules:
            trigger = rule.evaluate(readings)
            if trigger is not None:
                readings[f"_rule_{rule.rule_name}"] = trigger  # type: ignore[assignment]

        previous = self.state  # type: ignore[attr-defined]
        desired = self.compute_desired_state(readings)

        if desired is not None and desired != previous:
            self._transition_to(desired)

        current = self.state  # type: ignore[attr-defined]

        if current != previous:
            severity = self._severity_map.get(current, "warning")
            await self.doc.alog_event(
                "state_change",
                {"from": previous, "to": current},
                severity=severity,
            )
            await self.bus.publish(
                DomainEvent(
                    event_type="state.changed",
                    source_layer="reactive",
                    source_asset=self.config.asset_id,
                    payload={"from": previous, "to": current},
                    severity=severity,
                )
            )
            self.log.info("FSM state: %s → %s", previous, current)

            # Escalate non-nominal transitions to the intelligent layer.
            # The MAS subscribes to this event and triggers a targeted investigation
            # rather than waiting for the next polling cycle.
            if severity in ("warning", "critical"):
                await self.bus.publish(
                    DomainEvent(
                        event_type="reactive.escalation_requested",
                        source_layer="reactive",
                        source_asset=self.config.asset_id,
                        payload={
                            "from_state": previous,
                            "to_state":   current,
                            "severity":   severity,
                            "question": (
                                f"The reactive FSM transitioned from {previous} to {current} "
                                f"(severity={severity}). Investigate the cause using available "
                                f"sensor data and the knowledge graph. What is driving this state "
                                f"change and what management action is recommended?"
                            ),
                        },
                        severity=severity,
                    )
                )

        await self.cache.aset_state(current)
        return current

    def _transition_to(self, target_state: str) -> None:
        """Trigger the auto-generated ``_goto_<state>`` transition."""
        if target_state not in self._states:
            self.log.warning("Unknown state requested: %s", target_state)
            return
        trigger = getattr(self, f"_goto_{target_state}", None)
        if trigger is not None:
            trigger()

    async def start(self) -> None:
        self._running = True
        self.log.info(
            "MultiStateFSMRuleEngine started (states=%s, interval=%ds)",
            self._states,
            self.eval_interval,
        )
        while self._running:
            try:
                await self.evaluate()
            except Exception as exc:
                self.log.error("Rule engine error: %s", exc)
            await asyncio.sleep(self.eval_interval)

    def get_state(self) -> str:
        # self.state is injected at runtime by transitions.Machine; mypy cannot see it.
        return self.state  # type: ignore[attr-defined]
