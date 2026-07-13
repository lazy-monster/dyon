"""SSE streaming endpoint for agent interactions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.services.base import ServiceRegistry

log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    stream: bool = True


def build_chat_router(
    config: TwinConfig,
    service_registry: ServiceRegistry | None = None,
    *,
    agent=None,
) -> APIRouter:
    """Serve ``POST /chat`` backed by a single conversational agent.

    Pass ``agent`` to bind a specific agent (e.g. the dashboard chat agent);
    otherwise the agent is resolved per request as the highest-priority agent of
    the registry's ``intelligent`` service, preserving the original behaviour.
    """
    router = APIRouter()

    def _resolve_agent():
        if agent is not None:
            return agent
        if service_registry is None:
            return None
        try:
            mas_svc = service_registry.get("intelligent")
        except KeyError:
            return None
        return getattr(mas_svc, "agents", [None])[0]

    @router.post("/chat")
    async def chat(req: ChatRequest):
        try:
            ag = _resolve_agent()
            if ag is None:
                return JSONResponse(status_code=503, content={"detail": "no agent available"})

            if req.stream:
                async def _generate() -> AsyncGenerator[str, None]:
                    # Flush a first byte immediately so the browser sees a live
                    # connection rather than waiting on a silent socket.
                    yield ": open\n\n"
                    try:
                        if hasattr(ag, "ask_stream"):
                            async for chunk in ag.ask_stream(req.message):
                                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                        else:
                            # A tool-calling agent's ``ask`` runs for many seconds
                            # (tool calls + LLM) and yields nothing until done.
                            # Run it as a task and emit SSE keepalive comments
                            # while we wait, so the connection (and the browser's
                            # fetch) stays alive instead of erroring out.
                            task = asyncio.ensure_future(ag.ask(req.message))
                            while not task.done():
                                done, _ = await asyncio.wait({task}, timeout=5.0)
                                if not done:
                                    yield ": keepalive\n\n"
                            response = await task
                            yield f"data: {json.dumps({'chunk': response})}\n\n"
                        yield "data: [DONE]\n\n"
                    except Exception:
                        # The stream has already started (status committed), so we
                        # still emit an SSE frame — but keep it generic and log the
                        # real error server-side.
                        log.exception("Chat stream error")
                        yield f"data: {json.dumps({'error': 'internal error'})}\n\n"

                return StreamingResponse(
                    _generate(), media_type="text/event-stream"
                )
            else:
                response = await ag.ask(req.message)
                return {"response": response}

        except Exception:
            log.exception("Chat endpoint error")
            return JSONResponse(status_code=500, content={"detail": "internal error"})

    return router
