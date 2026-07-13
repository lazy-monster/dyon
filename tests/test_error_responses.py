"""Error paths never leak exception internals to (possibly unauthenticated) callers.

The chat endpoint and the visualization routers log the real error server-side
and return a generic body; the STT upload is size-capped so a hostile caller
cannot read an unbounded body into memory.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.events import EventBus
from dyon.services.api.streaming import build_chat_router
from dyon.services.base import ServiceRegistry
from dyon.visualization.serve import mount_visualization
from dyon.visualization.voice import register_voice_provider


class _BrokenAgent:
    async def ask(self, message: str) -> str:
        raise RuntimeError("secret-internal-path /etc/passwd")


class _RaisingStore:
    async def aquery_recent_fields(self, fields, minutes=60, measurement="asset_telemetry"):
        raise RuntimeError("mongodb://admin:password@host down")

    async def aget_latest_fields(self, fields, measurement="asset_telemetry"):
        raise RuntimeError("mongodb://admin:password@host down")


class _StoreService:
    service_name = "data"
    dependencies: list = []

    def __init__(self, ts):
        self.ts = ts
        self.doc = None
        self.bus = EventBus()


def _config():
    return TwinConfig(sensor_fields=[SensorFieldSpec(name="temp", nominal=20.0)])


def test_chat_non_stream_error_is_generic():
    app = FastAPI()
    app.include_router(build_chat_router(_config(), None, agent=_BrokenAgent()))
    c = TestClient(app)
    r = c.post("/chat", json={"message": "hi", "stream": False})
    assert r.status_code == 500
    assert "secret-internal-path" not in r.text
    assert r.json()["detail"] == "internal error"


def test_viz_history_error_is_generic():
    app = FastAPI()
    registry = ServiceRegistry()
    registry.register(_StoreService(_RaisingStore()))
    mount_visualization(app, _config(), registry, serve_dashboard=False)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/viz/history?fields=temp&minutes=60")
    assert r.status_code == 500
    assert "mongodb" not in r.text.lower()


def test_stt_rejects_oversized_upload():
    class _FakeVoice:
        async def transcribe(self, audio, content_type):
            return "should not reach here"

        async def synthesize(self, text):
            return b"", "audio/wav"

    register_voice_provider(_FakeVoice())
    try:
        app = FastAPI()
        registry = ServiceRegistry()
        registry.register(_StoreService(_RaisingStore()))
        mount_visualization(app, _config(), registry, serve_dashboard=False)
        c = TestClient(app)
        oversized = b"\x00" * (10 * 1024 * 1024 + 1)
        r = c.post("/api/viz/voice/stt", files={"audio": ("a.webm", oversized, "audio/webm")})
        assert r.status_code == 413
    finally:
        register_voice_provider(None)  # type: ignore[arg-type]
