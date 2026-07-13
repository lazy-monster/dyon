"""DittoConnector: cross-twin queries via Eclipse Ditto REST API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class DittoConnector:
    """Cross-twin queries via Eclipse Ditto REST API (services layer)."""

    connector_type = "ditto"
    layer = "services"

    def __init__(
        self,
        config: TwinConfig,
        remote_ditto_url: str | None = None,
        known_twins: list[str] | set[str] | None = None,
    ):
        """
        Parameters
        ----------
        config           : TwinConfig (used for Ditto URL/auth defaults)
        remote_ditto_url : Override the Ditto base URL (for remote instances)
        known_twins      : If given, ``can_reach`` only returns True for IDs in
                           this set. Useful in multi-Ditto deployments where
                           several DittoConnectors are registered, each scoped
                           to a different remote. If ``None`` (default), the
                           connector claims it can reach any twin — the actual
                           call is then verified by the live HTTP request.
        """
        self._cfg = config.ditto
        self.ditto_url = remote_ditto_url or self._cfg.url
        self._auth = (self._cfg.user, self._cfg.password)
        self._namespace = self._cfg.namespace
        self.known_twins = set(known_twins) if known_twins is not None else None
        # Reuse one client across calls instead of a handshake per request.
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
        """Cheap reachability check (no network I/O).

        Returns True if the target ID is in the configured ``known_twins``
        set, or if no set was configured (optimistic default — match the
        first registered DittoConnector). Live verification happens inside
        ``query()`` / ``push()``, which raise on transport failure.

        Use :py:meth:`can_reach_async` for an actual live HTTP probe.
        """
        if self.known_twins is None:
            return bool(target_twin_id)
        return target_twin_id in self.known_twins

    async def can_reach_async(self, target_twin_id: str) -> bool:
        """Live HTTP probe — call from async code when you actually need it."""
        thing_id = f"{self._namespace}:{target_twin_id}"
        try:
            r = await self._http().get(
                f"{self.ditto_url}/api/2/things/{thing_id}",
                auth=self._auth,
                timeout=5.0,
            )
            return r.status_code == 200
        except Exception:
            return False

    async def query(self, target_twin_id: str, request: dict) -> dict:
        thing_id = f"{self._namespace}:{target_twin_id}"
        feature = request.get("feature", "telemetry")
        r = await self._http().get(
            f"{self.ditto_url}/api/2/things/{thing_id}"
            f"/features/{feature}/properties",
            auth=self._auth,
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    async def push(self, target_twin_id: str, data: dict) -> None:
        thing_id = f"{self._namespace}:{target_twin_id}"
        # Copy so we don't mutate the caller's dict when extracting metadata.
        payload = dict(data)
        feature = payload.pop("_feature", "external_input")
        await self._http().patch(
            f"{self.ditto_url}/api/2/things/{thing_id}"
            f"/features/{feature}/properties",
            json=payload,
            auth=self._auth,
            timeout=10.0,
        )

    async def subscribe(
        self,
        target_twin_id: str,
        event_type: str,
        handler: Callable[[dict], Awaitable[None]],
    ) -> None:
        log.warning(
            "DittoConnector.subscribe not implemented — use MQTT or SSE for streaming"
        )
