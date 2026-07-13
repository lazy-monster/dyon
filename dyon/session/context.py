"""Generic session context — transient, stateful context shared across L4/L5/L6.

A session represents a bounded interaction (a conversation, a production run,
a maintenance window, a clinical consultation) that spans multiple events but
has a defined start and end.

Domain-specific implementations subclass SessionContext and add their own fields.
Example:
    class MaintenanceSessionContext(SessionContext):
        technician_id: str = ""
        active_procedure: str = ""
        ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from dyon.data.storage.base import CacheStore

log = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """
    Generic session context.

    Fields are intentionally minimal and domain-neutral:
    - primary_entity_id   — the main entity this session is about
                            (customer, patient, device, operator, …)
    - secondary_entity_id — a secondary entity involved
                            (product, treatment, job, …)
    - phase               — current FSM phase as a plain string
                            (any value — defined by the domain)
    - health_score        — generic 0–1 quality/health indicator
    - alert_count         — number of alerts raised this session
    - event_history       — ordered list of events (role/type + content)
    - outcome             — how the session ended (domain-defined string)
    - extra               — arbitrary domain-specific metadata
    """

    session_id:           str            = field(default_factory=lambda: str(uuid.uuid4()))
    primary_entity_id:    str            = ""
    secondary_entity_id:  str            = ""

    phase:                str            = "initial"
    health_score:         float          = 1.0
    alert_count:          int            = 0

    event_history:        list[dict]     = field(default_factory=list)
    outcome:              str            = ""

    started_at:           float          = field(default_factory=time.time)
    last_updated:         float          = field(default_factory=time.time)
    extra:                dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def add_event(self, event_type: str, content: Any,
                  metadata: dict | None = None) -> None:
        """Append an event to the session history."""
        self.event_history.append({
            "type":    event_type,
            "content": content,
            "ts":      time.time(),
            **(metadata or {}),
        })
        self.last_updated = time.time()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SessionContext:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_T = TypeVar("_T", bound=SessionContext)


class SessionStore(Generic[_T]):
    """
    Cache-backed session store.

    Persists every session as a JSON blob under ``session:<id>``.  Works with
    any object whose attribute access matches the ``CacheStore`` protocol
    (``set_latest`` / ``get_latest_cached``). The Redis-specific features
    (TTL, ``KEYS``, ``DELETE``) are used when the underlying client exposes
    them — falling back gracefully otherwise so the store works on any cache.
    """

    PREFIX = "session:"
    DEFAULT_TTL = 3600  # seconds

    def __init__(self, cache: CacheStore, ttl: int = DEFAULT_TTL,
                 context_class: type[_T] = SessionContext) -> None:  # type: ignore[assignment]
        self._cache = cache
        self._ttl = ttl
        self._cls = context_class

    def _key(self, session_id: str) -> str:
        return f"{self.PREFIX}{session_id}"

    def _native_client(self):
        """Return the underlying redis client if present, else None."""
        return getattr(self._cache, "_client", None)

    def save(self, ctx: _T) -> None:
        raw = json.dumps(ctx.to_dict(), default=str)
        key = self._key(ctx.session_id)
        client = self._native_client()
        if client is not None and hasattr(client, "setex"):
            client.setex(key, self._ttl, raw)
        else:
            # Fallback: store via the CacheStore protocol. TTL is not supported
            # in the protocol — callers needing expiry should use a Redis-backed cache.
            self._cache.set_latest(key, raw)

    def load(self, session_id: str) -> _T | None:
        key = self._key(session_id)
        client = self._native_client()
        if client is not None and hasattr(client, "get"):
            raw = client.get(key)
        else:
            raw = self._cache.get_latest_cached(key)
        if raw is None:
            return None
        if isinstance(raw, bytes | bytearray):
            raw = raw.decode()
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw  # already-decoded JSON object
        return self._cls.from_dict(data)  # type: ignore[return-value]

    def delete(self, session_id: str) -> None:
        client = self._native_client()
        key = self._key(session_id)
        if client is not None and hasattr(client, "delete"):
            client.delete(key)
        # No protocol-level delete; non-Redis caches will silently retain the key.

    def list_active(self) -> list[_T]:
        """Return all currently live sessions.

        Requires the underlying cache to expose ``keys`` (Redis does). Returns
        an empty list otherwise.
        """
        client = self._native_client()
        if client is None or not hasattr(client, "keys"):
            return []
        results: list[_T] = []
        for key in client.keys(f"{self.PREFIX}*"):
            raw = client.get(key)
            if raw is None:
                continue
            try:
                if isinstance(raw, bytes | bytearray):
                    raw = raw.decode()
                data = json.loads(raw)
                # from_dict is declared on the base class, so mypy sees its
                # return as SessionContext rather than the bound _T.
                results.append(self._cls.from_dict(data))  # type: ignore[arg-type]
            except Exception as e:
                log.warning("Skipping unreadable session record %r: %s", key, e)
        return results

    def new_session(self, **kwargs: Any) -> _T:
        ctx = self._cls(**kwargs)
        self.save(ctx)
        return ctx

    # --- async wrappers -------------------------------------------------------
    # The cache client is synchronous; these offload it to a worker thread so the
    # hot layer loops never block on a Redis round trip. Sync methods stay for
    # sync callers (existing user code, thread-offloaded tools).

    async def asave(self, ctx: _T) -> None:
        await asyncio.to_thread(self.save, ctx)

    async def aload(self, session_id: str) -> _T | None:
        return await asyncio.to_thread(self.load, session_id)

    async def alist_active(self) -> list[_T]:
        return await asyncio.to_thread(self.list_active)
