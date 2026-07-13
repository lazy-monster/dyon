"""Eclipse Ditto REST client: policy + Thing CRUD."""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class DittoClient:
    """Async client for Eclipse Ditto Thing API."""

    def __init__(self, config: TwinConfig):
        self._cfg = config.ditto
        self._asset_id = config.asset_id
        self._thing_id = config.thing_id
        self._auth = (self._cfg.user, self._cfg.password)
        self._base = f"{self._cfg.url}/api/2"
        # One reusable client per DittoClient — httpx.AsyncClient is designed for
        # reuse, so the 5 s sync cycle no longer pays a TCP/TLS handshake per call.
        # Created lazily on first use (inside the running loop).
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def wait_for_ready(self, timeout: int = 120) -> None:
        """Wait until the Ditto gateway responds to HTTP requests.

        A TCP check is not sufficient — nginx starts before the Ditto gateway
        is ready to serve requests. Instead, poll /health until we get any HTTP
        response: 200 means fully healthy, 503 means degraded but the gateway
        is up (optional services like connectivity/search may be absent).
        """
        import time
        health_url = f"{self._cfg.url}/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                async with nullcontext(self._http()) as client:
                    r = await client.get(health_url, timeout=5.0)
                    if r.status_code in (200, 503):
                        log.info("Ditto gateway ready (HTTP %d)", r.status_code)
                        return
            except Exception:
                pass
            await asyncio.sleep(3)
        log.warning("Ditto did not become ready within %ds", timeout)

    async def create_policy(self) -> None:
        policy_id = f"{self._cfg.namespace}:{self._asset_id}_policy"
        payload = {
            "entries": {
                "owner": {
                    "subjects": {
                        f"nginx:{self._cfg.user}": {"type": "nginx basic auth user"}
                    },
                    "resources": {
                        "policy:/": {"grant": ["READ", "WRITE"], "revoke": []},
                        "thing:/": {"grant": ["READ", "WRITE"], "revoke": []},
                        "message:/": {"grant": ["READ", "WRITE"], "revoke": []},
                    },
                }
            }
        }
        async with nullcontext(self._http()) as client:
            r = await client.put(
                f"{self._base}/policies/{policy_id}",
                json=payload,
                auth=self._auth,
                timeout=10.0,
            )
            if r.status_code not in (200, 201, 204):
                log.warning("Ditto create_policy: %d %s", r.status_code, r.text[:200])

    async def create_thing(self, config: TwinConfig) -> None:
        policy_id = f"{self._cfg.namespace}:{self._asset_id}_policy"
        payload = {
            "policyId": policy_id,
            "attributes": {
                "asset_id": config.asset_id,
                "asset_type": config.asset_type,
                "asset_name": config.asset_name,
            },
            "features": {
                "telemetry": {"properties": {}},
                "health": {
                    "properties": {
                        "health_score": 100.0,
                        "operational_state": "running",
                    }
                },
            },
        }
        async with nullcontext(self._http()) as client:
            r = await client.put(
                f"{self._base}/things/{self._thing_id}",
                json=payload,
                auth=self._auth,
                timeout=10.0,
            )
            if r.status_code not in (200, 201, 204):
                log.warning("Ditto create_thing: %d %s", r.status_code, r.text[:200])
            else:
                log.info("Ditto Thing '%s' ready", self._thing_id)

    async def get_thing(self) -> dict:
        async with nullcontext(self._http()) as client:
            r = await client.get(
                f"{self._base}/things/{self._thing_id}",
                auth=self._auth,
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()

    async def update_feature(self, feature: str, properties: dict) -> None:
        async with nullcontext(self._http()) as client:
            r = await client.put(
                f"{self._base}/things/{self._thing_id}"
                f"/features/{feature}/properties",
                json=properties,
                auth=self._auth,
                timeout=10.0,
            )
            if r.status_code not in (200, 201, 204):
                log.debug(
                    "Ditto update_feature '%s': %d", feature, r.status_code
                )

    async def get_feature(self, feature: str) -> dict:
        async with nullcontext(self._http()) as client:
            r = await client.get(
                f"{self._base}/things/{self._thing_id}"
                f"/features/{feature}/properties",
                auth=self._auth,
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()

    async def get_thing_feature(self, thing_id: str, feature: str) -> dict:
        """Read *any* Ditto thing's feature (not just this client's own thing)."""
        async with nullcontext(self._http()) as client:
            r = await client.get(
                f"{self._base}/things/{thing_id}/features/{feature}/properties",
                auth=self._auth,
                timeout=5.0,
            )
            r.raise_for_status()
            return r.json()

    async def update_thing_feature(
        self, thing_id: str, feature: str, properties: dict
    ) -> None:
        """Update *any* Ditto thing's feature (not just this client's own thing)."""
        async with nullcontext(self._http()) as client:
            r = await client.put(
                f"{self._base}/things/{thing_id}/features/{feature}/properties",
                json=properties,
                auth=self._auth,
                timeout=5.0,
            )
            if r.status_code not in (200, 201, 204):
                log.debug(
                    "Ditto update_thing_feature '%s' on '%s': %d",
                    feature, thing_id, r.status_code,
                )
