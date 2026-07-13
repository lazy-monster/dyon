"""Optional server-side voice endpoints.

``POST /api/viz/voice/stt`` transcribes uploaded audio; ``POST /api/viz/voice/tts``
synthesises speech from text. Both require a registered
:class:`~dyon.visualization.voice.VoiceProvider`; with none installed they return
``501`` and the client falls back to the browser's Web Speech API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from dyon.visualization.voice import get_voice_provider

if TYPE_CHECKING:
    from dyon.visualization.context import VizContext

log = logging.getLogger(__name__)

# Cap the STT upload so an unauthenticated (or hostile) caller cannot read an
# unbounded body into memory.
_MAX_AUDIO_BYTES = 10 * 1024 * 1024


class TtsRequest(BaseModel):
    text: str


def build_voice_router(ctx: VizContext) -> APIRouter:
    router = APIRouter()

    @router.post("/voice/stt")
    async def stt(audio: UploadFile):
        provider = get_voice_provider()
        if provider is None:
            raise HTTPException(501, "No server-side voice provider; use Web Speech")
        data = await audio.read(_MAX_AUDIO_BYTES + 1)
        if len(data) > _MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio too large (max 10 MB)")
        try:
            text = await provider.transcribe(data, audio.content_type or "audio/webm")
        except Exception:
            log.exception("STT transcription failed")
            raise HTTPException(500, "internal error") from None
        return {"text": text}

    @router.post("/voice/tts")
    async def tts(req: TtsRequest):
        provider = get_voice_provider()
        if provider is None:
            raise HTTPException(501, "No server-side voice provider; use Web Speech")
        try:
            audio_bytes, content_type = await provider.synthesize(req.text)
        except Exception:
            log.exception("TTS synthesis failed")
            raise HTTPException(500, "internal error") from None
        return Response(content=audio_bytes, media_type=content_type)

    return router
