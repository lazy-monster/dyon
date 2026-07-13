"""APIConnector: cross-twin queries via FastAPI REST endpoints."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)


class APIConnector:
    """Cross-twin queries via FastAPI REST endpoints (intelligent layer)."""

    connector_type = "api"
    layer = "intelligent"

    def __init__(self, twin_endpoints: dict[str, str]):
        """
        twin_endpoints: {"twin_002": "http://host:8502", ...}
        """
        self.endpoints = twin_endpoints
        # One reusable client instead of a TCP+TLS handshake per call. Created
        # lazily on first use (inside the running loop) and closed via aclose().
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def can_reach(self, target_twin_id: str) -> bool:
        return target_twin_id in self.endpoints

    async def query(self, target_twin_id: str, request: dict) -> dict:
        url = self.endpoints[target_twin_id]
        question = request.get("question", "What is the current asset health?")
        try:
            r = await self._http().post(
                f"{url}/api/chat",
                json={"message": question, "stream": False},
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error("APIConnector query to '%s' failed: %s", target_twin_id, e)
            return {"error": str(e)}

    async def push(self, target_twin_id: str, data: dict) -> None:
        url = self.endpoints[target_twin_id]
        try:
            await self._http().post(
                f"{url}/api/twin/external",
                json=data,
                timeout=10.0,
            )
        except Exception as e:
            log.debug("APIConnector push to '%s' failed: %s", target_twin_id, e)

    async def subscribe(
        self,
        target_twin_id: str,
        event_type: str,
        handler: Callable[[dict], Awaitable[None]],
    ) -> None:
        log.warning("APIConnector.subscribe not supported — use MQTTConnector")
