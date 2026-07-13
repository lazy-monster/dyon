"""Server-side detection of which optional visualization features are available.

The client uses this to decide whether to offer the 3D viewport or server-side
voice, and the relevant endpoints use it to return ``501`` rather than crashing
when an optional extra is not installed. Detection is cheap and import-guarded so
the core install pulls in nothing new.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def capabilities() -> dict[str, bool]:
    """Return a map of optional feature -> availability.

    - ``forecast``: a forecasting backend (Prophet) is importable.
    - ``voice_server``: a server-side STT/TTS backend is importable.
    - ``scene3d``: the 3D path renders client-side, so this is always available;
      capability gating happens in the browser (WebGL2 check).
    """
    return {
        "forecast": _installed("prophet"),
        "voice_server": _installed("faster_whisper") or _installed("whisper"),
        "scene3d": True,
    }
