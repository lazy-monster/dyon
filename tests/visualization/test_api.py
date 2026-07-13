"""The viz routes mount cleanly, stay opt-in, and read through the discovered
stores."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from vizfakes import make_config, make_registry

from dyon.services.api.app import create_app
from dyon.visualization.serve import mount_visualization


def _paths(app):
    """The effective, prefix-applied paths the app serves.

    Read from the OpenAPI schema rather than ``app.routes``: FastAPI wraps each
    ``include_router`` call in an opaque object that reports the router-relative
    path, so ``/api/chat`` appears there only as ``/chat``.
    """
    return set(app.openapi()["paths"])


def _chat_route_count(app):
    """How many chat endpoints are bound.

    The OpenAPI view keys on path and so collapses a duplicate registration;
    counting the underlying routes is what catches the viz layer re-mounting a
    chat route that the core already bound.
    """
    count, stack = 0, list(app.routes)
    while stack:
        route = stack.pop()
        if getattr(route, "path", "").endswith("/chat"):
            count += 1
        inner = getattr(route, "original_router", None)
        if inner is not None:
            stack.extend(inner.routes)
    return count


def _client(spec=None):
    config = make_config()
    registry, _bus, _ts = make_registry()
    app = FastAPI()
    mount_visualization(app, config, registry, spec=spec, serve_dashboard=False)
    return TestClient(app), config


def test_spec_endpoint_returns_derived_dashboard():
    client, _config = _client()
    resp = client.get("/api/viz/spec")
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset_id"] == "pump1"
    assert any(p["kind"] == "kpi" for p in body["panels"])


def test_capabilities_endpoint():
    client, _ = _client()
    body = client.get("/api/viz/capabilities").json()
    assert set(body) == {"forecast", "voice_server", "scene3d"}
    assert body["scene3d"] is True


def test_history_endpoint_normalizes_points():
    client, _ = _client()
    body = client.get("/api/viz/history?fields=temp&minutes=60").json()
    assert "temp" in body
    point = body["temp"][0]
    assert set(point) == {"t", "v"}


def test_history_defaults_to_all_fields():
    client, _ = _client()
    body = client.get("/api/viz/history").json()
    assert {"temp", "moisture"} <= set(body)


def test_snapshot_reports_alarm_states():
    client, _ = _client()
    body = client.get("/api/viz/snapshot").json()
    # temp=80 > warn(70) but < crit(90); moisture=5 < crit(10) on a "below" rule.
    assert "temp" in body["alarms"]["warn"]
    assert "moisture" in body["alarms"]["crit"]


def test_forecast_returns_501_without_backend():
    # The anaconda test env may or may not have Prophet; either a real forecast
    # (200) or a clean 501 is acceptable — never a 500.
    client, _ = _client()
    resp = client.get("/api/viz/forecast?field=temp&steps=6")
    assert resp.status_code in (200, 501)


def test_forecast_unknown_field_is_404_or_501():
    client, _ = _client()
    resp = client.get("/api/viz/forecast?field=nope")
    assert resp.status_code in (404, 501)


# --- Backward-compatibility guard -----------------------------------------

def test_create_app_default_has_no_viz_routes():
    config = make_config()
    registry, _, _ = make_registry()
    app = create_app(config, registry)
    paths = _paths(app)
    assert not any(p.startswith("/api/viz") for p in paths)


def test_create_app_include_viz_mounts_routes():
    config = make_config()
    registry, _, _ = make_registry()
    app = create_app(config, registry, include_viz=True)
    paths = _paths(app)
    assert "/api/viz/spec" in paths


# --- chat-agent wiring ------------------------------------------------------

class _StubAgent:
    """A minimal bound chat agent: only the async ``ask`` the router needs."""

    async def ask(self, message: str) -> str:
        return "stub-answer"


def test_create_app_chat_agent_backs_a_single_chat_route():
    # With include_chat (default) and include_viz both on, /api/chat must be
    # bound exactly once — backed by the passed agent, not re-mounted by viz.
    config = make_config()
    registry, _, _ = make_registry()
    app = create_app(config, registry, include_viz=True, chat_agent=_StubAgent())
    assert _chat_route_count(app) == 1
    body = TestClient(app).post(
        "/api/chat", json={"message": "hi", "stream": False}
    ).json()
    assert body["response"] == "stub-answer"


def test_create_app_viz_mounts_chat_when_core_chat_disabled():
    # include_chat off, but a viz dashboard still needs its chat: the viz mount
    # binds /api/chat against the agent instead.
    config = make_config()
    registry, _, _ = make_registry()
    app = create_app(
        config, registry, include_chat=False, include_viz=True,
        chat_agent=_StubAgent(),
    )
    assert _chat_route_count(app) == 1
    body = TestClient(app).post(
        "/api/chat", json={"message": "hi", "stream": False}
    ).json()
    assert body["response"] == "stub-answer"
