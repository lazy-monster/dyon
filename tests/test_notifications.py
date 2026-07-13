"""HumanNotifier fan-out formatting, and the webhook URL-redaction fix.

A webhook URL often embeds a secret token (Slack), so the success log must show
only the host, never the full URL.
"""

from __future__ import annotations

import httpx

from dyon.notifications.notifier import HumanNotifier, WebhookBackend


class RecordingBackend:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def send(self, subject, body, context):
        self.calls.append((subject, body, context))


async def test_notifier_dispatches_formatted_message_to_all_backends():
    a, b = RecordingBackend(), RecordingBackend()
    notifier = HumanNotifier([a, b])
    await notifier.send("overheating", {"asset": "pump_1", "temp": 95})
    for backend in (a, b):
        assert len(backend.calls) == 1
        subject, body, _ = backend.calls[0]
        assert "Human intervention required" in subject
        assert "Reason: overheating" in body
        assert "asset: pump_1" in body


async def test_one_failing_backend_does_not_sink_the_others():
    class Boom(RecordingBackend):
        async def send(self, subject, body, context):
            raise RuntimeError("down")

    ok = RecordingBackend()
    await HumanNotifier([Boom(), ok]).send("x")
    assert len(ok.calls) == 1               # gather(return_exceptions=True) isolates the failure


async def test_webhook_logs_host_only_not_full_url(monkeypatch, caplog):
    class _Resp:
        def raise_for_status(self):
            pass

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    secret_url = "https://hooks.slack.com/services/T000/B000/SUPERSECRETTOKEN"
    with caplog.at_level("INFO"):
        await WebhookBackend(secret_url).send("subj", "body", {})
    logged = " ".join(r.message for r in caplog.records)
    assert "hooks.slack.com" in logged
    assert "SUPERSECRETTOKEN" not in logged
