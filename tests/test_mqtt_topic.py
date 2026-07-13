"""Tests for MQTTTransport._topic_matches against the MQTT wildcard spec.

``+`` matches exactly one level; ``#`` matches the rest of the topic.
"""

from __future__ import annotations

import pytest

from dyon.network.transport import MQTTTransport

m = MQTTTransport._topic_matches


@pytest.mark.parametrize("pattern,topic,expected", [
    # exact
    ("dt/a1/telemetry", "dt/a1/telemetry", True),
    ("dt/a1/telemetry", "dt/a2/telemetry", False),
    # single-level +
    ("dt/+/telemetry", "dt/a1/telemetry", True),
    ("dt/+/telemetry", "dt/a1/control", False),
    ("dt/+/telemetry", "dt/a1/x/telemetry", False),   # + spans one level only
    # multi-level #
    ("dt/#", "dt/a1/telemetry", True),
    ("dt/#", "dt/a1/x/y/z", True),
    ("dt/a1/#", "dt/a2/telemetry", False),
    # + and # combined
    ("dt/+/#", "dt/a1/telemetry/raw", True),
    # length mismatches
    ("dt/a1/telemetry", "dt/a1", False),
    ("dt/+", "dt/a1/telemetry", False),
])
def test_topic_matches(pattern, topic, expected):
    assert m(pattern, topic) is expected
