"""MQTT transport: TLS wiring, reconnect backoff, and initial-connect retry.

A fake paho client records what the transport configures and can be told to fail
the first N connect attempts, so the retry loop is exercised without a broker.
"""

from __future__ import annotations

import paho.mqtt.client as paho
import pytest

from dyon.core.config import TwinConfig
from dyon.network.transport import MQTTTransport


class FakePahoClient:
    instances: list[FakePahoClient] = []

    def __init__(self, *a, **k):
        self.tls_set_calls: list = []
        self.tls_insecure = None
        self.reconnect_delay = None
        self.connect_attempts = 0
        self.loop_started = False
        self.fail_first = 0
        FakePahoClient.instances.append(self)

    # paho surface the transport touches
    def tls_set(self, ca_certs=None):
        self.tls_set_calls.append(ca_certs)

    def tls_insecure_set(self, v):
        self.tls_insecure = v

    def reconnect_delay_set(self, min_delay, max_delay):
        self.reconnect_delay = (min_delay, max_delay)

    def username_pw_set(self, u, p):
        pass

    def connect(self, broker, port, keepalive):
        self.connect_attempts += 1
        if self.connect_attempts <= self.fail_first:
            raise OSError("broker not up yet")

    def loop_start(self):
        self.loop_started = True


@pytest.fixture(autouse=True)
def _fake_paho(monkeypatch):
    FakePahoClient.instances = []
    monkeypatch.setattr(paho, "Client", FakePahoClient)
    # paho 2.x CallbackAPIVersion access must not explode
    yield


def test_reconnect_backoff_is_configured():
    MQTTTransport(TwinConfig())
    client = FakePahoClient.instances[-1]
    assert client.reconnect_delay == (1, 60)


def test_tls_enabled_calls_tls_set():
    MQTTTransport(TwinConfig(mqtt={"tls": True, "tls_insecure": True}))
    client = FakePahoClient.instances[-1]
    assert client.tls_set_calls == [None]     # "" ca_certs -> None (system default)
    assert client.tls_insecure is True


def test_tls_disabled_by_default():
    MQTTTransport(TwinConfig())
    assert FakePahoClient.instances[-1].tls_set_calls == []


def test_initial_connect_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)   # no real backoff sleeps
    t = MQTTTransport(TwinConfig())
    client = FakePahoClient.instances[-1]
    client.fail_first = 2                     # fail twice, succeed on the third
    t.connect(retries=5, base_delay=0.0)
    assert client.connect_attempts == 3
    assert client.loop_started is True


def test_initial_connect_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    t = MQTTTransport(TwinConfig())
    client = FakePahoClient.instances[-1]
    client.fail_first = 99
    with pytest.raises(ConnectionError):
        t.connect(retries=3, base_delay=0.0)
    assert client.connect_attempts == 3
