"""MQTTConnector: cross-twin messaging via shared MQTT broker."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class MQTTConnector:
    """Cross-twin messaging via a shared MQTT broker (data layer)."""

    connector_type = "mqtt"
    layer = "data"

    def __init__(
        self,
        config: TwinConfig,
        known_twins: list[str] | None = None,
    ):
        import paho.mqtt.client as mqtt

        self._cfg = config.mqtt
        self._asset_id = config.asset_id
        self.known_twins = set(known_twins or [])
        # paho-mqtt 2.x requires CallbackAPIVersion; we use VERSION1 to keep
        # the (client, userdata, flags, rc) callback signatures.
        try:
            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"dyon_conn_{config.asset_id}",
            )
        except (AttributeError, TypeError):
            self._client = mqtt.Client(client_id=f"dyon_conn_{config.asset_id}")
        self._client.on_connect = lambda c, ud, f, rc: log.debug(
            "MQTTConnector connected: rc=%d", rc
        )
        # paho handles broker drops with capped exponential backoff.
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        if self._cfg.username:
            self._client.username_pw_set(self._cfg.username, self._cfg.password)
        if self._cfg.tls:
            self._client.tls_set(ca_certs=self._cfg.tls_ca_certs or None)
            if self._cfg.tls_insecure:
                self._client.tls_insecure_set(True)
        # Loop captured the first time subscribe() runs from an async context.
        # Connection is deferred to connect() so the object can be constructed
        # in non-async contexts (e.g. tests, scripts).
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def connect(self, retries: int = 5, base_delay: float = 1.0) -> None:
        """Connect with capped exponential backoff on the initial attempt.

        Reconnects after a drop are paho's job (reconnect_delay_set); this
        loop only covers the broker not being up yet at first use.
        """
        if self._connected:
            return
        import time

        for attempt in range(retries):
            try:
                self._client.connect(self._cfg.broker, self._cfg.port, 60)
                self._client.loop_start()
                self._connected = True
                return
            except OSError as e:
                delay = min(base_delay * 2 ** attempt, 30.0)
                log.warning("MQTTConnector connect to %s:%d failed (%s); retry in %.0fs",
                            self._cfg.broker, self._cfg.port, e, delay)
                time.sleep(delay)
        raise ConnectionError(
            f"MQTT broker {self._cfg.broker}:{self._cfg.port} unreachable after {retries} attempts"
        )

    def can_reach(self, target_twin_id: str) -> bool:
        return target_twin_id in self.known_twins

    async def query(self, target_twin_id: str, request: dict) -> dict:
        log.warning(
            "MQTTConnector.query is fire-and-forget; use DittoConnector for request/reply"
        )
        await self.push(target_twin_id, request)
        return {}

    async def push(self, target_twin_id: str, data: dict) -> None:
        # connect() blocks (socket connect + retry sleeps); keep the loop free.
        await asyncio.to_thread(self.connect)
        topic = f"dt/{target_twin_id}/external"
        payload = json.dumps({**data, "_source": self._asset_id}, default=str)
        self._client.publish(topic, payload, qos=1)

    async def subscribe(
        self,
        target_twin_id: str,
        event_type: str,
        handler: Callable[[dict], Awaitable[None]],
    ) -> None:
        await asyncio.to_thread(self.connect)
        # Capture the running loop once so paho's threaded callback can hand
        # work back to asyncio without calling the deprecated get_event_loop().
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        topic = f"dt/{target_twin_id}/{event_type}"
        self.known_twins.add(target_twin_id)
        loop = self._loop

        def _on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode())
                asyncio.run_coroutine_threadsafe(handler(payload), loop)  # type: ignore[arg-type]
            except Exception as e:
                log.error("MQTTConnector message error: %s", e)

        self._client.subscribe(topic, qos=1)
        self._client.message_callback_add(topic, _on_message)
        log.info("MQTTConnector subscribed to '%s'", topic)

    def close(self) -> None:
        if not self._connected:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False
