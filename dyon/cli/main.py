"""CLI: dyon init / infra up / infra check / run / train"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import click


def _to_class_name(asset_type: str) -> str:
    """Convert ``"centrifugal_pump-v2"`` → ``"CentrifugalPumpV2Twin"`` safely.

    Splits on any non-alphanumeric character and discards empty pieces so that
    any input produces a valid Python identifier.
    """
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", asset_type) if p]
    name = "".join(p[:1].upper() + p[1:] for p in parts) or "Generic"
    if name[0].isdigit():
        name = f"_{name}"
    return f"{name}Twin"


@click.group()
@click.version_option(package_name="dyon")
def cli():
    """Dyon — domain-agnostic digital twin framework."""


@cli.command()
@click.option("--asset-type", default="generic_asset", help="Asset type identifier")
@click.option("--name", default="My Asset", help="Human-readable asset name")
@click.option("--asset-id", default="asset_001", help="Unique asset ID")
@click.option("--out", default=".", help="Output directory")
def init(asset_type: str, name: str, asset_id: str, out: str) -> None:
    """Scaffold a new digital twin project."""
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)

    env_content = f"""# Dyon configuration for {name}
DT_ASSET_ID={asset_id}
DT_ASSET_TYPE={asset_type}
DT_ASSET_NAME={name}

# Infrastructure — nested fields use __ (double-underscore) as the delimiter.
# TwinConfig reads these via pydantic-settings env_nested_delimiter="__".
# Single-underscore keys (DT_MQTT_BROKER) are silently ignored.
DT_MQTT__BROKER=localhost
DT_MQTT__PORT=1883

DT_INFLUX__URL=http://localhost:8086
DT_INFLUX__TOKEN=my-super-secret-token
DT_INFLUX__ORG=digital_twin
DT_INFLUX__BUCKET=asset_telemetry

DT_MONGO__URI=mongodb://admin:password@localhost:27017
DT_MONGO__DB=digital_twin

DT_REDIS__URL=redis://localhost:6379

DT_MINIO__ENDPOINT=localhost:9000
DT_MINIO__ACCESS_KEY=minioadmin
DT_MINIO__SECRET_KEY=minioadmin

DT_DITTO__URL=http://localhost:8080
DT_DITTO__USER=ditto
DT_DITTO__PASSWORD=ditto
DT_DITTO__NAMESPACE=org.example

DT_NEO4J__URI=bolt://localhost:7687
DT_NEO4J__USER=neo4j
DT_NEO4J__PASSWORD=password

# LLM (for intelligent layer)
DT_LLM__PROVIDER=openai
DT_LLM__MODEL=gpt-4o-mini
DT_LLM__API_KEY=

# API server
DT_API_PORT=8500
"""

    twin_py = f'''"""
Auto-generated Dyon twin scaffold for {name}.
Customise build_layers() to wire your asset-specific logic.
"""

import asyncio
import logging
from dyon.core.config import TwinConfig, SensorFieldSpec
from dyon.core.base import AbstractDigitalTwin
from dyon.core.lifecycle import TwinLifecycle
from dyon.data import InfluxAdapter, MongoAdapter, RedisAdapter
from dyon.data.writer import TelemetryRouter
from dyon.data.management import DataManagementPipeline
from dyon.network import MQTTIngestor
from dyon.services.ditto.client import DittoClient
from dyon.services.ditto.sync import DittoSyncService
from dyon.reactive import ThresholdRuleEngine
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

config = TwinConfig(
    sensor_fields=[
        # Add your sensor fields here, e.g.:
        # SensorFieldSpec(name="temperature_c", nominal=25.0, noise_std=0.5,
        #                 warn_threshold=60.0, crit_threshold=75.0),
    ]
)


class {_to_class_name(asset_type)}(AbstractDigitalTwin):
    def build_layers(self):
        ts = InfluxAdapter(self.config)
        doc = MongoAdapter(self.config)
        cache = RedisAdapter(self.config)
        ditto = DittoClient(self.config)
        router = TelemetryRouter(self.config, self.bus,
                                  ts_store=ts, doc_store=doc, cache=cache)
        return {{
            "data": router,
            "network": MQTTIngestor(self.config, self.bus, router=router),
            "data_mgmt": DataManagementPipeline(self.config, self.bus,
                                                 ts_store=ts, cache=cache),
            "services": DittoSyncService(self.config, self.bus,
                                          ts_store=ts, cache=cache,
                                          ditto_client=ditto),
            "reactive": ThresholdRuleEngine(self.config, self.bus,
                                             ts_store=ts, cache=cache,
                                             doc_store=doc),
        }}


if __name__ == "__main__":
    twin = {_to_class_name(asset_type)}(config)
    lifecycle = TwinLifecycle()
    lifecycle.add(twin)
    asyncio.run(lifecycle.run_forever())
'''

    (out_path / ".env").write_text(env_content)
    (out_path / "twin.py").write_text(twin_py)

    click.echo(f"Scaffolded twin project in '{out_path}'")
    click.echo("  .env      — environment configuration")
    click.echo("  twin.py   — twin class scaffold")
    click.echo("\nNext steps:")
    click.echo("  1. Edit .env and twin.py for your asset")
    click.echo("  2. dyon infra up")
    click.echo("  3. python twin.py")


@cli.group()
def infra():
    """Infrastructure management commands."""


@infra.command("up")
@click.option("--layers", default="data,network,services", help="Comma-separated active layers")
@click.option("--out", default="docker-compose.yml", help="Output file path")
@click.option("--generate-only", is_flag=True, default=False,
              help="Write docker-compose.yml without starting containers")
def infra_up(layers: str, out: str, generate_only: bool) -> None:
    """Generate docker-compose.yml and start the infrastructure stack."""
    import subprocess

    from dotenv import load_dotenv

    from dyon.core.config import TwinConfig
    from dyon.infra.docker import DockerComposeGenerator

    load_dotenv()
    config = TwinConfig()
    active = [layer.strip() for layer in layers.split(",")]
    gen = DockerComposeGenerator()
    compose = gen.generate(config, active)
    Path(out).write_text(compose)
    click.echo(f"Written {out}")
    for companion in gen.write_companion_files(out, active, config):
        click.echo(f"Written {companion}")
    Path(".dyon-layers").write_text(",".join(active))

    if generate_only:
        click.echo("Skipping container start (--generate-only). Run: docker compose up -d")
        return

    click.echo("Starting infrastructure containers...")
    result = subprocess.run(
        ["docker", "compose", "-f", out, "up", "-d"],
        capture_output=False,
    )
    if result.returncode != 0:
        click.echo(
            "docker compose up -d failed — see output above. "
            "Ensure Docker is running and the compose file is valid.",
            err=True,
        )
        sys.exit(result.returncode)
    click.echo("Infrastructure started. Run: dyon infra check")


@infra.command("check")
@click.option("--layers", default=None,
              help="Comma-separated active layers (default: read from .dyon-layers)")
def infra_check(layers: str) -> None:
    """Check infrastructure readiness."""
    from dotenv import load_dotenv

    from dyon.core.config import TwinConfig
    from dyon.infra.health_check import InfraHealthChecker

    if layers is None:
        # Prefer the current state file; fall back to the legacy .dtforge-layers
        # name so a project scaffolded before the rename still resolves.
        state_file = Path(".dyon-layers")
        if not state_file.exists():
            state_file = Path(".dtforge-layers")
        layers = state_file.read_text().strip() if state_file.exists() else "data,network,services"

    load_dotenv()
    config = TwinConfig()
    active = [layer.strip() for layer in layers.split(",")]
    checker = InfraHealthChecker(config)

    async def _run():
        results = await checker.check_all(active)
        checker.print_report(results)
        if not all(results.values()):
            sys.exit(1)

    asyncio.run(_run())


@cli.command()
@click.argument("twin_module", default="twin")
def run(twin_module: str) -> None:
    """Run the digital twin (imports twin_module and calls run_forever)."""
    import importlib

    from dotenv import load_dotenv

    load_dotenv()
    try:
        mod = importlib.import_module(twin_module)
    except ModuleNotFoundError:
        click.echo(f"Cannot import '{twin_module}'. Run from the project directory.")
        sys.exit(1)

    if not hasattr(mod, "twin") and not hasattr(mod, "lifecycle"):
        click.echo(f"Module '{twin_module}' has no 'twin' or 'lifecycle' attribute.")
        sys.exit(1)

    lifecycle = getattr(mod, "lifecycle", None)
    if lifecycle:
        asyncio.run(lifecycle.run_forever())
    else:
        twin = mod.twin
        asyncio.run(twin.run())


@cli.command()
@click.option("--timesteps", default=100_000, type=int, help="Training timesteps")
@click.option("--algorithm", default="SAC", help="SB3 algorithm (SAC, TD3, PPO, A2C)")
@click.option("--save", default="policy", help="Policy save name")
@click.option("--env-module", default="twin_env", help="Module with 'env' Gymnasium environment")
def train(timesteps: int, algorithm: str, save: str, env_module: str) -> None:
    """Train an RL policy for the autonomous layer."""
    import importlib

    from dyon.autonomous.trainer import PolicyTrainer

    try:
        mod = importlib.import_module(env_module)
        env = mod.env
    except (ModuleNotFoundError, AttributeError) as e:
        click.echo(f"Cannot load environment from '{env_module}': {e}")
        sys.exit(1)

    trainer = PolicyTrainer(env, algorithm=algorithm)
    trainer.train(total_timesteps=timesteps)
    path = trainer.save(save)
    click.echo(f"Policy saved to '{path}'")


@cli.command()
@click.option("--api", default="http://localhost:8500",
              help="Base URL of the running twin's API (serves /api/viz/*)")
@click.option("--port", default=8600, type=int, help="Local port to serve the dashboard on")
@click.option("--open/--no-open", "open_browser", default=True,
              help="Open the dashboard in a browser")
def dashboard(api: str, port: int, open_browser: bool) -> None:
    """Serve the framework dashboard, pointed at a running twin's API.

    The dashboard is a static, CDN-based client; this command serves the bundled
    assets locally and points them at ``--api`` (CORS is open on the twin API),
    so the twin itself needs no extra wiring beyond ``include_viz=True`` or a
    ``mount_visualization`` call.
    """
    import functools
    import http.server
    import webbrowser
    from pathlib import Path

    assets = Path(__file__).resolve().parent.parent / "visualization" / "assets"
    if not assets.is_dir():
        click.echo("Dashboard assets not found; is dyon installed correctly?")
        sys.exit(1)

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(assets)
    )
    url = f"http://localhost:{port}/?api={api}"
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    click.echo(f"Dyon dashboard → {url}")
    click.echo(f"(serving {assets} · pointing at {api} · Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nDashboard stopped.")
        server.shutdown()


if __name__ == "__main__":
    cli()
