"""Infrastructure readiness checks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class InfraHealthChecker:
    """
    Checks that all required infrastructure services are reachable
    before starting the digital twin.
    """

    def __init__(self, config: TwinConfig):
        self._cfg = config

    async def check_all(self, layers: list[str]) -> dict[str, bool]:
        results: dict[str, bool] = {}
        tasks: dict[str, asyncio.Task] = {}

        tasks["mqtt"] = asyncio.create_task(self._check_mqtt())

        if "data" in layers or "network" in layers:
            import urllib.parse
            _mongo = urllib.parse.urlparse(self._cfg.mongo.uri)
            _redis = urllib.parse.urlparse(self._cfg.redis.url)
            tasks["influxdb"] = asyncio.create_task(
                self._check_http(self._cfg.influx.url + "/health"))
            tasks["mongodb"] = asyncio.create_task(
                self._check_tcp(_mongo.hostname or "localhost", _mongo.port or 27017))
            tasks["redis"] = asyncio.create_task(
                self._check_tcp(_redis.hostname or "localhost", _redis.port or 6379))

        if "services" in layers or "service_ditto" in layers:
            # Ditto's /health returns 503 when optional services (connectivity,
            # search) are absent, even though the gateway is fully functional.
            # Use a TCP check on the nginx proxy port instead.
            import urllib.parse
            _ditto_host = urllib.parse.urlparse(self._cfg.ditto.url).hostname or "localhost"
            _ditto_port = urllib.parse.urlparse(self._cfg.ditto.url).port or 8080
            tasks["ditto"] = asyncio.create_task(self._check_tcp(_ditto_host, _ditto_port))

        if "intelligent" in layers:
            # urlsplit handles bolt+s://, embedded credentials, and a missing
            # port cleanly, where a naive replace/split would choke.
            import urllib.parse
            _neo = urllib.parse.urlsplit(self._cfg.neo4j.uri)
            host = _neo.hostname or "localhost"
            port = _neo.port or 7687
            tasks["neo4j"] = asyncio.create_task(self._check_tcp(host, port))

        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception:
                results[name] = False

        return results

    async def _check_mqtt(self) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._cfg.mqtt.broker, self._cfg.mqtt.port),
                timeout=3.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def _check_tcp(self, host: str, port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def _check_http(self, url: str) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=5.0)
                return r.status_code < 500
        except Exception:
            return False

    def print_report(self, results: dict[str, bool]) -> None:
        ok = all(results.values())
        status_line = "READY" if ok else "NOT READY"
        print(f"\nInfrastructure status: {status_line}")
        for service, healthy in results.items():
            icon = "✓" if healthy else "✗"
            print(f"  {icon} {service}")
        print()
