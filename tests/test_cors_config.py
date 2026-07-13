"""CORS is open in dev mode and restricted to configured origins in production.

Dev keeps the zero-config federation experience (any dashboard origin); a
production twin only answers the origins explicitly listed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from dyon.core.config import TwinConfig
from dyon.services.api.app import create_app
from dyon.services.base import ServiceRegistry


def _app(cfg):
    return TestClient(create_app(cfg, ServiceRegistry(), include_chat=False))


def _allow_origin(client, origin):
    r = client.get("/health", headers={"origin": origin})
    return r.headers.get("access-control-allow-origin")


def test_dev_mode_allows_any_origin():
    c = _app(TwinConfig())
    assert _allow_origin(c, "https://anything.example") == "*"


def test_production_only_allows_listed_origins():
    cfg = TwinConfig(security={
        "mode": "production", "api_key": "k",
        "cors_origins": ["https://dash.example.com"],
    })
    c = _app(cfg)
    assert _allow_origin(c, "https://dash.example.com") == "https://dash.example.com"
    # A non-listed origin gets no allow-origin header echoed back.
    assert _allow_origin(c, "https://evil.example") != "https://evil.example"


def test_production_without_origins_sends_no_wildcard():
    cfg = TwinConfig(security={"mode": "production", "api_key": "k"})
    c = _app(cfg)
    assert _allow_origin(c, "https://x.example") != "*"
