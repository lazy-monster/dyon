"""The dashboard chat router streams a tool-calling agent's answer.

A tool-calling agent exposes only ``ask`` (no token-level ``ask_stream``) and can
run for many seconds before returning. The router must flush a first byte
immediately and keep the SSE connection alive while it waits, then deliver the
full answer and a terminating ``[DONE]`` — otherwise the browser's fetch reports
a network error on long queries.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dyon.core.config import TwinConfig
from dyon.services.api.streaming import build_chat_router


class _SlowAgent:
    """Only ``ask`` (the long, blocking path the router must wrap)."""

    async def ask(self, message: str) -> str:
        await asyncio.sleep(0.05)
        return "the answer"


class _BrokenAgent:
    async def ask(self, message: str) -> str:
        raise RuntimeError("kaboom")


def _client(agent) -> TestClient:
    app = FastAPI()
    app.include_router(build_chat_router(TwinConfig(), None, agent=agent))
    return TestClient(app)


def test_stream_flushes_first_byte_then_full_answer():
    r = _client(_SlowAgent()).post("/chat", json={"message": "status", "stream": True})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    # An immediate keepalive comment leads the stream (proves the wait-and-keep-
    # alive path ran), the answer arrives, and the stream terminates cleanly.
    assert body.startswith(": open")
    assert '"chunk": "the answer"' in body
    assert "data: [DONE]" in body


def test_stream_signals_errors_without_leaking_internals():
    # The stream has already committed a 200 before the agent fails, so the
    # router still emits an SSE error frame — but a generic one: the underlying
    # exception text must never reach an (unauthenticated) client.
    body = _client(_BrokenAgent()).post(
        "/chat", json={"message": "x", "stream": True}
    ).text
    assert '"error"' in body
    assert "internal error" in body
    assert "kaboom" not in body


def test_non_stream_returns_plain_json():
    body = _client(_SlowAgent()).post(
        "/chat", json={"message": "x", "stream": False}
    ).json()
    assert body["response"] == "the answer"
