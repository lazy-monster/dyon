"""Voice provider contract for optional server-side speech.

The default voice path is entirely client-side (the browser's Web Speech API),
so voice works with no server compute and no extra install. These hooks exist
for deployments that want higher-quality server-side STT/TTS: implement
:class:`VoiceProvider`, register it, and the endpoints in
:mod:`dyon.visualization.api.voice` use it. With no provider registered the
endpoints return ``501`` and the client silently falls back to Web Speech.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class VoiceProvider(Protocol):
    """Server-side speech-to-text / text-to-speech backend."""

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        """Return the transcript of the given audio bytes."""

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return ``(audio_bytes, content_type)`` for the given text."""


_PROVIDER: VoiceProvider | None = None


def register_voice_provider(provider: VoiceProvider) -> None:
    """Install the process-wide server-side voice provider."""
    global _PROVIDER
    _PROVIDER = provider


def get_voice_provider() -> VoiceProvider | None:
    return _PROVIDER
