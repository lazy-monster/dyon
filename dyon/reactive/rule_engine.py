"""ThresholdRuleEngine with FSM powered by the transitions library."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from transitions import Machine

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent
from dyon.reactive.base import Rule
from dyon.reactive.rule_repository import rule_from_persisted

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore, TimeSeriesStore
    from dyon.reactive.rule_repository import RuleRepository

log = logging.getLogger(__name__)


class ThresholdRuleEngine(LayerBase):
    """
    Evaluates per-field threshold rules and drives a finite state machine.

    States: running → warning → shutdown
    Custom Rule objects can be added alongside the built-in threshold checks.
    """

    layer_name = "reactive"

    _states = ["running", "warning", "shutdown"]
    _transitions = [
        {"trigger": "raise_warning", "source": "running", "dest": "warning"},
        {"trigger": "shutdown_asset", "source": ["running", "warning"], "dest": "shutdown"},
        {"trigger": "recover", "source": "warning", "dest": "running"},
        {"trigger": "restart", "source": "shutdown", "dest": "running"},
    ]

    def __init__(
        self,
        config: TwinConfig,
        event_bus: EventBus,
        *,
        ts_store: TimeSeriesStore,
        cache: CacheStore,
        doc_store: DocumentStore,
        custom_rules: list[Rule] | None = None,
        eval_interval: int = 5,
        rule_repository: RuleRepository | None = None,
        rule_fn_registry: dict | None = None,
        rule_reload_interval: float = 60.0,
        consecutive_crit_to_latch: int = 1,
    ):
        super().__init__(config, event_bus)
        self.ts = ts_store
        self.cache = cache
        self.doc = doc_store
        self.custom_rules = custom_rules or []
        self.eval_interval = eval_interval
        # Require this many consecutive critical evaluations before latching
        # shutdown, so a single noisy sample can't kill the twin. Default 1
        # preserves the original immediate-latch behaviour.
        self._consecutive_crit_to_latch = max(1, consecutive_crit_to_latch)
        self._crit_streak = 0
        # Optional PostgreSQL-backed dynamic rules (hot-reloaded). When no
        # repository is supplied the engine behaves exactly as before — this is
        # entirely opt-in and needs no database.
        self._repo = rule_repository
        self._rule_fn_registry = rule_fn_registry or {}
        self._rule_reload_interval = rule_reload_interval
        self._dynamic_rules: list[Rule] = []
        self._last_rule_reload = 0.0

        # ignore_invalid_triggers prevents the library from raising MachineError
        # when a trigger is fired that has no transition from the current state.
        Machine(
            model=self,
            states=self._states,
            initial="running",
            transitions=self._transitions,  # type: ignore[arg-type]
            ignore_invalid_triggers=True,
        )

    async def evaluate(self) -> str:
        """Run threshold + custom rules, drive FSM, return current state."""
        crit, warn = 0, 0

        # One batched query for every field, off the event loop — replaces the
        # former N+M separate blocking get_latest calls per cycle.
        readings = await self.ts.aget_latest_fields(self.config.field_names)

        for field, t in self.config.thresholds.items():
            val = readings.get(field)
            if val is None:
                continue
            low = t.get("low", False)
            crit_t, warn_t = t.get("crit"), t.get("warn")
            if crit_t is not None and ((low and val < crit_t) or (not low and val > crit_t)):
                crit += 1
            elif warn_t is not None and ((low and val < warn_t) or (not low and val > warn_t)):
                warn += 1

        for rule in (*self.custom_rules, *self._dynamic_rules):
            trigger = rule.evaluate(readings)
            if trigger == "critical":
                crit += 1
            elif trigger == "warning":
                warn += 1

        self._crit_streak = self._crit_streak + 1 if crit > 0 else 0
        previous = self.state  # type: ignore[attr-defined]

        if (
            self._crit_streak >= self._consecutive_crit_to_latch
            and self.state != "shutdown"  # type: ignore[attr-defined]
        ):
            self.shutdown_asset()  # type: ignore[attr-defined]
        elif warn > 0 and self.state == "running":  # type: ignore[attr-defined]
            self.raise_warning()  # type: ignore[attr-defined]
        elif warn == 0 and self.state == "warning":  # type: ignore[attr-defined]
            self.recover()  # type: ignore[attr-defined]

        current = self.state  # type: ignore[attr-defined]

        if current != previous:
            severity = "critical" if current == "shutdown" else "warning"
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
            self.log.info("State: %s → %s", previous, current)

        await self.cache.aset_state(current)
        return current

    async def initialise(self) -> None:
        # Load any persisted dynamic rules once before the loop starts.
        await self.reload_rules()

    async def reload_rules(self) -> None:
        """Refresh the dynamic rule set from the repository (no-op without one)."""
        if self._repo is None:
            return
        try:
            persisted = await self._repo.load_active_rules()
            self._dynamic_rules = [
                rule_from_persisted(p, self._rule_fn_registry) for p in persisted
            ]
            self.log.info(
                "Loaded %d dynamic rule(s) from repository", len(self._dynamic_rules)
            )
        except Exception as e:
            self.log.error("Rule reload failed (keeping previous set): %s", e)
        self._last_rule_reload = time.monotonic()

    async def start(self) -> None:
        self._running = True
        self.log.info("ThresholdRuleEngine started (interval=%ds)", self.eval_interval)
        while self._running:
            try:
                await self.evaluate()
            except Exception as e:
                self.log.error("Rule engine error: %s", e)
            # Hot-reload dynamic rules periodically without blocking evaluation.
            if self._repo is not None and (
                time.monotonic() - self._last_rule_reload >= self._rule_reload_interval
            ):
                await self.reload_rules()
            await asyncio.sleep(self.eval_interval)

    def get_state(self) -> str:
        # self.state is injected at runtime by transitions.Machine; mypy cannot see it.
        return self.state  # type: ignore[attr-defined]

    async def acknowledge_and_restart(self) -> str:
        """Human-acknowledged recovery from the latched ``shutdown`` state.

        ``shutdown`` latches by design — a safety stop must not silently clear
        itself when readings transiently return to normal. This is the explicit,
        audited path back to ``running``: wire it to an operator endpoint or a
        human-approved OODA action. No-op (returns the current state) unless the
        engine is actually latched in shutdown.
        """
        if self.state != "shutdown":  # type: ignore[attr-defined]
            return self.state  # type: ignore[attr-defined]
        self.restart()  # type: ignore[attr-defined]  # FSM trigger: shutdown → running
        self._crit_streak = 0
        current = self.state  # type: ignore[attr-defined]
        await self.doc.alog_event(
            "shutdown_acknowledged",
            {"from": "shutdown", "to": current},
            severity="warning",
        )
        await self.cache.aset_state(current)
        self.log.warning("Shutdown latch acknowledged by operator — now '%s'", current)
        return current
