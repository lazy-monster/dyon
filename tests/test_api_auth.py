"""API-key middleware guards every HTTP and WebSocket route when a key is set.

The key is accepted as an ``x-api-key`` header or an ``api_key`` query parameter
(EventSource/WebSocket clients cannot set headers). ``/health`` and the static
dashboard stay exempt. With no key configured the middleware is not installed and
the dev experience is unchanged.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from dyon.core.config import TwinConfig
from dyon.services.api.app import create_app
from dyon.services.base import ServiceRegistry


def _client(**security):
    cfg = TwinConfig(security=security) if security else TwinConfig()
    app = create_app(cfg, ServiceRegistry(), include_chat=False)
    return TestClient(app)


def test_health_is_exempt():
    c = _client(api_key="s3cret")
    assert c.get("/health").status_code != 401


def test_api_route_blocked_without_key():
    c = _client(api_key="s3cret")
    assert c.get("/api/twin/state").status_code == 401
    assert "internal" not in c.get("/api/twin/state").text.lower()


def test_api_route_allowed_with_header():
    c = _client(api_key="s3cret")
    assert c.get("/api/twin/state", headers={"x-api-key": "s3cret"}).status_code != 401


def test_api_route_allowed_with_query_param():
    c = _client(api_key="s3cret")
    assert c.get("/api/twin/state?api_key=s3cret").status_code != 401


def test_wrong_key_rejected():
    c = _client(api_key="s3cret")
    assert c.get("/api/twin/state", headers={"x-api-key": "nope"}).status_code == 401


def test_dev_mode_leaves_routes_open():
    c = _client()          # no key configured
    assert c.get("/api/twin/state").status_code != 401
