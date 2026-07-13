"""Process-local counters for framework health reporting.

Deliberately minimal: a named-counter registry the /health endpoint can dump.
Not a metrics platform — implementations that want Prometheus can export
these numbers themselves.
"""

from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_counters: Counter = Counter()


def increment(name: str, n: int = 1) -> None:
    with _lock:
        _counters[name] += n


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def reset() -> None:      # for tests
    with _lock:
        _counters.clear()
