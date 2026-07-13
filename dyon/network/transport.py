"""MQTT transport wrapper (publish + subscribe)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class MQTTTransport:
    """Thin wrapper around paho-mqtt for publish/subscribe."""

    def __init__(self, config: TwinConfig, role: str = "client"):
        import paho.mqtt.client as mqtt

        self._cfg = config.mqtt
        self._asset_id = config.asset_id
        # paho-mqtt 2.x requires CallbackAPIVersion; we use VERSION1 to keep
        # the (client, userdata, flags, rc) callback signatures used below.
        try:
            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"dyon_{config.asset_id}_{role}",
            )
        except (AttributeError, TypeError):
            # paho-mqtt 1.x fallback
            self._client = mqtt.Client(client_id=f"dyon_{config.asset_id}_{role}")
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._subscriptions: dict[str, Callable[[dict], None]] = {}
        # paho handles broker drops with capped exponential backoff; the
        # re-subscribe on reconnect is already wired in _on_connect.
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        if self._cfg.username:
            self._client.username_pw_set(self._cfg.username, self._cfg.password)

        if self._cfg.tls:
            self._client.tls_set(ca_certs=self._cfg.tls_ca_certs or None)
            if self._cfg.tls_insecure:
                self._client.tls_insecure_set(True)

    def connect(self, retries: int = 5, base_delay: float = 1.0) -> None:
        """Connect with capped exponential backoff on the initial attempt.

        Reconnects after a drop are paho's job (reconnect_delay_set); this
        loop only covers the broker not being up yet at twin start.
        """
        import time

        for attempt in range(retries):
            try:
                self._client.connect(self._cfg.broker, self._cfg.port, self._cfg.keepalive)
                self._client.loop_start()
                return
            except OSError as e:
                delay = min(base_delay * 2 ** attempt, 30.0)
                log.warning("MQTT connect to %s:%d failed (%s); retry in %.0fs",
                            self._cfg.broker, self._cfg.port, e, delay)
                time.sleep(delay)
        raise ConnectionError(
            f"MQTT broker {self._cfg.broker}:{self._cfg.port} unreachable after {retries} attempts"
        )

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: dict | str, qos: int = 1) -> None:
        if isinstance(payload, dict):
            payload = json.dumps(payload, default=str)
        result = self._client.publish(topic, payload, qos=qos)
        if result.rc != 0:
            log.warning("MQTT publish failed on '%s': rc=%d", topic, result.rc)

    def subscribe(
        self, topic: str, callback: Callable[[dict], None], qos: int = 1
    ) -> None:
        self._subscriptions[topic] = callback
        self._client.subscribe(topic, qos=qos)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("MQTT connected to %s:%d", self._cfg.broker, self._cfg.port)
            # Re-subscribe after reconnect
            for topic in self._subscriptions:
                client.subscribe(topic, qos=1)
        else:
            log.error("MQTT connect failed, rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            log.warning("MQTT unexpected disconnect, rc=%d", rc)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        callback = self._subscriptions.get(topic)
        # Try wildcard match
        if callback is None:
            for pattern, cb in self._subscriptions.items():
                if self._topic_matches(pattern, topic):
                    callback = cb
                    break
        if callback is None:
            return
        try:
            payload = json.loads(msg.payload.decode())
            callback(payload)
        except Exception as e:
            log.error("MQTT message handler error on '%s': %s", topic, e)

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        parts_p = pattern.split("/")
        parts_t = topic.split("/")
        if "#" not in pattern and "+" not in pattern:
            return pattern == topic
        i = 0
        for pp in parts_p:
            if pp == "#":
                return True
            if i >= len(parts_t):
                return False
            if pp != "+" and pp != parts_t[i]:
                return False
            i += 1
        return i == len(parts_t)
