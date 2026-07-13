"""MQTTIngestor validates payloads, dead-letters bad ones, and drops pre-loop msgs.

No broker: the transport is replaced with a fake that records publishes, and the
router with a fake that records routed payloads.
"""

from __future__ import annotations

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.events import EventBus
from dyon.network.ingestor import MQTTIngestor


class FakeTransport:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic, payload, qos=1):
        self.published.append((topic, payload))


class FakeRouter:
    def __init__(self):
        self.routed: list[dict] = []

    async def route(self, data):
        self.routed.append(data)


def _ingestor(validator=None):
    cfg = TwinConfig(sensor_fields=[SensorFieldSpec(name="temp", nominal=20.0)])
    ing = MQTTIngestor(cfg, EventBus(), router=FakeRouter(), schema_validator=validator)
    ing._transport = FakeTransport()
    return ing


def test_invalid_payload_is_dead_lettered():
    def _bad_validator(payload):
        raise ValueError("schema mismatch")

    ing = _ingestor(_bad_validator)
    ing._on_message({"temp": "not-a-number"})
    assert len(ing._transport.published) == 1
    topic, body = ing._transport.published[0]
    assert topic.endswith("/dead_letter")
    assert body["payload"] == {"temp": "not-a-number"}


def test_message_before_loop_is_dropped_not_routed(caplog):
    ing = _ingestor()             # default validator, _loop still None
    with caplog.at_level("WARNING"):
        ing._on_message({"temp": 21.0})
    # Nothing dead-lettered (payload was valid) and nothing routed (no loop yet).
    assert ing._transport.published == []
    assert ing.router.routed == []
    assert any("before event loop" in r.message for r in caplog.records)


def test_default_validator_keeps_numeric_fields():
    ing = _ingestor()
    kept = ing._default_validator({"temp": 21.5, "note": "hello", "fault_injected": 1})
    assert kept == {"temp": 21.5, "fault_injected": 1}
