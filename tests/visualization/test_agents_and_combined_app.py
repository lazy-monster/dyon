"""The agents endpoint surfaces the MAS snapshot, and the combined-twin app
serves a federated spec + the static client without any stores behind it."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from vizfakes import make_config, make_registry

from dyon.visualization.derive import derive_combined_spec
from dyon.visualization.schema import MemberRef
from dyon.visualization.serve import (
    create_combined_dashboard_app,
    mount_visualization,
)


class _FakeAgent:
    def __init__(self, name, domain, priority=10):
        self.agent_name = name
        self.domain = domain
        self.priority = priority


class _FakeMAS:
    interval = 60

    def __init__(self):
        self.agents = [_FakeAgent("a1", "d1"), _FakeAgent("a2", "d2")]
        self._detail = {
            "a1": {
                "domain": "d1",
                "observations": {"anomaly_detected": True},
                "findings": {"severity": "warning", "action": "alert", "summary": "hot"},
                "tool_calls": [{"tool": "read", "input": {"f": "temp"}, "output": "80"}],
                "ts_s": 123,
            },
        }

    def get_agent_detail(self, name):
        return self._detail.get(name, {})


def test_agents_endpoint_reports_mas():
    config = make_config()
    registry, _, _ = make_registry()
    app = FastAPI()
    mount_visualization(app, config, registry, mas=_FakeMAS())
    client = TestClient(app)

    resp = client.get("/api/viz/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["monitor_interval"] == 60
    assert {a["agent_name"] for a in data["agents"]} == {"a1", "a2"}

    a1 = next(a for a in data["agents"] if a["agent_name"] == "a1")
    assert a1["anomaly"] is True
    assert a1["severity"] == "warning"
    assert a1["action"] == "alert"
    assert a1["tool_calls"][0]["tool"] == "read"


def test_agents_endpoint_without_mas_reports_unavailable():
    config = make_config()
    registry, _, _ = make_registry()   # registers a 'data' service, not 'intelligent'
    app = FastAPI()
    mount_visualization(app, config, registry)
    client = TestClient(app)

    data = client.get("/api/viz/agents").json()
    assert data["available"] is False
    assert data["agents"] == []


def test_mount_visualization_base_path_namespaces_routes():
    """A member twin mounted under a base path answers there and nowhere else, so
    several twins can share one origin in a bundled composite dashboard."""
    config = make_config()
    registry, _, _ = make_registry()
    app = FastAPI()
    mount_visualization(
        app, config, registry, mas=_FakeMAS(),
        serve_dashboard=False, base_path="/boiler",
    )
    client = TestClient(app)

    assert client.get("/boiler/api/viz/spec").status_code == 200
    assert client.get("/boiler/api/viz/agents").json()["available"] is True
    # The default (unprefixed) routes are not mounted for a base-path member.
    assert client.get("/api/viz/spec").status_code == 404


def test_combined_app_surfaces_composite_mas():
    """A composite overseer's own agents are served by the combined app when its
    MAS is passed, so the overview's Agents tab has something to show."""
    spec = derive_combined_spec(
        combination="composite", asset_id="station", asset_name="Power Station",
        members=[MemberRef(id="boiler", name="Boiler", api_base="/boiler")],
        hierarchy={"station": ["boiler"]},
    )
    app = create_combined_dashboard_app(spec, mas=_FakeMAS())
    client = TestClient(app)

    data = client.get("/api/viz/agents").json()
    assert data["available"] is True
    assert {a["agent_name"] for a in data["agents"]} == {"a1", "a2"}


def test_combined_app_serves_federated_spec_and_client():
    spec = derive_combined_spec(
        combination="composite", asset_id="station", asset_name="Power Station",
        members=[MemberRef(id="boiler", name="Boiler", api_base="http://h:8501")],
        hierarchy={"station": ["boiler"]},
    )
    app = create_combined_dashboard_app(spec)
    client = TestClient(app)

    served = client.get("/api/viz/spec").json()
    assert served["combination"] == "composite"
    assert served["members"][0]["id"] == "boiler"
    assert served["panels"] == []

    assert client.get("/api/viz/capabilities").status_code == 200
    dash = client.get("/dashboard/")
    assert dash.status_code == 200
    assert "dyon-combined.js" in dash.text
