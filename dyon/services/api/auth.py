"""API-key middleware guarding HTTP and WebSocket routes."""

from __future__ import annotations

import hmac
import logging
from urllib.parse import parse_qs

log = logging.getLogger(__name__)

_EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc", "/dashboard")


class ApiKeyMiddleware:
    """Reject requests lacking the configured key.

    Accepts the key as an ``x-api-key`` header or an ``api_key`` query
    parameter (EventSource/WebSocket clients cannot set headers). Comparison
    is constant-time. Non-HTTP scopes (lifespan) pass through untouched.
    """

    def __init__(self, app, api_key: str):
        self.app = app
        self._key = api_key.encode()

    def _provided_key(self, scope) -> bytes:
        for name, value in scope.get("headers", []):
            if name == b"x-api-key":
                return value
        qs = parse_qs(scope.get("query_string", b"").decode())
        if "api_key" in qs:
            return qs["api_key"][0].encode()
        return b""

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or any(
            scope["path"].startswith(p) for p in _EXEMPT_PREFIXES
        ):
            await self.app(scope, receive, send)
            return
        if hmac.compare_digest(self._provided_key(scope), self._key):
            await self.app(scope, receive, send)
            return
        log.warning("Rejected unauthenticated %s %s", scope["type"], scope["path"])
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await send({
            "type": "http.response.start", "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"missing or invalid API key"}',
        })
