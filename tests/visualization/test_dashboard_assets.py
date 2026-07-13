"""The framework-owned dashboard is served as static assets and the
``dyon dashboard`` CLI command is wired in."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from vizfakes import make_config, make_registry

from dyon.cli.main import cli
from dyon.visualization.serve import mount_visualization

ASSETS = Path(__file__).resolve().parents[2] / "dyon" / "visualization" / "assets"


def test_asset_files_exist():
    for name in (
        "index.html", "dyon-dash.js", "dyon-dash.css",
        "scene3d.js", "dyon-combined.js",
    ):
        assert (ASSETS / name).is_file(), f"missing asset {name}"


def test_index_loads_the_client_scripts():
    html = (ASSETS / "index.html").read_text()
    for src in ("dyon-dash.js", "scene3d.js", "dyon-combined.js"):
        assert src in html, f"index.html does not load {src}"


def test_dashboard_served_when_serve_dashboard_true():
    config = make_config()
    registry, _, _ = make_registry()
    app = FastAPI()
    mount_visualization(app, config, registry, serve_dashboard=True)
    client = TestClient(app)
    # The canonical directory URL resolves to index.html (html=True), so the
    # bare /dashboard/ entry point works without naming a file.
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert "dyon-dash.js" in resp.text


def test_dashboard_not_served_when_disabled():
    config = make_config()
    registry, _, _ = make_registry()
    app = FastAPI()
    mount_visualization(app, config, registry, serve_dashboard=False)
    client = TestClient(app)
    assert client.get("/dashboard/").status_code == 404


def test_dashboard_cli_command_registered():
    assert "dashboard" in cli.commands
    params = {p.name for p in cli.commands["dashboard"].params}
    assert {"api", "port", "open_browser"} <= params
