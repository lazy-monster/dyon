"""Built-in reactive actions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from dyon.data.storage.base import DocumentStore
    from dyon.network.transport import MQTTTransport

log = logging.getLogger(__name__)


@runtime_checkable
class Action(Protocol):
    """An action that can be triggered by the reactive layer."""

    action_name: str

    async def execute(self, context: dict) -> None: ...


class LogEventAction:
    """Logs a document event when triggered."""

    action_name = "log_event"

    def __init__(self, doc_store: DocumentStore):
        self._doc = doc_store

    async def execute(self, context: dict) -> None:
        await self._doc.alog_event(
            context.get("event_type", "reactive_action"),
            context,
            severity=context.get("severity", "info"),
        )


class PublishMQTTAction:
    """Publishes a message to an MQTT topic when triggered."""

    action_name = "publish_mqtt"

    def __init__(self, transport: MQTTTransport, topic: str):
        self._transport = transport
        self._topic = topic

    async def execute(self, context: dict) -> None:
        self._transport.publish(self._topic, context)
