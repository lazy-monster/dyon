"""Server voice is optional. With no provider the endpoints return 501 (the
client then uses Web Speech); a registered provider is exercised end to end."""

from __future__ import annotations

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from vizfakes import make_config, make_registry

from dyon.visualization.serve import mount_visualization
from dyon.visualization.voice import (
    VoiceProvider,
    get_voice_provider,
    register_voice_provider,
)


def _client():
    cfg = make_config()
    registry, _, _ = make_registry()
    app = FastAPI()
    mount_visualization(app, cfg, registry, serve_dashboard=False)
    return TestClient(app)


def test_stt_501_without_provider():
    import dyon.visualization.voice as v
    v._PROVIDER = None

    resp = _client().post(
        "/api/viz/voice/stt",
        files={"audio": ("a.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert resp.status_code == 501


def test_tts_501_without_provider():
    import dyon.visualization.voice as v
    v._PROVIDER = None
    resp = _client().post("/api/viz/voice/tts", json={"text": "hello"})
    assert resp.status_code == 501


class FakeVoiceProvider:
    async def transcribe(self, audio: bytes, content_type: str) -> str:
        return "transcribed"

    async def synthesize(self, text: str):
        return b"AUDIO", "audio/mpeg"


def test_provider_protocol_is_satisfied():
    assert isinstance(FakeVoiceProvider(), VoiceProvider)


def test_registered_provider_drives_endpoints():
    register_voice_provider(FakeVoiceProvider())
    try:
        assert get_voice_provider() is not None
        client = _client()
        stt = client.post(
            "/api/viz/voice/stt",
            files={"audio": ("a.webm", io.BytesIO(b"x"), "audio/webm")},
        )
        assert stt.status_code == 200
        assert stt.json()["text"] == "transcribed"
        tts = client.post("/api/viz/voice/tts", json={"text": "hi"})
        assert tts.status_code == 200
        assert tts.content == b"AUDIO"
    finally:
        import dyon.visualization.voice as v
        v._PROVIDER = None
