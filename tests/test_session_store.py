"""SessionStore round-trips sessions and skips corrupt records loudly.

Covers both the sync surface and the async wrappers, plus the hardening fix that
an unreadable blob is logged at WARNING and skipped rather than silently dropped.
"""

from __future__ import annotations

from dyon.session.context import SessionContext, SessionStore


class FakeRedis:
    """Just enough of the redis client the store reaches for: get/setex/keys."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def setex(self, key, ttl, value):
        self.kv[key] = value

    def set(self, key, value):
        self.kv[key] = value

    def get(self, key):
        return self.kv.get(key)

    def keys(self, pattern):
        # pattern is "session:*"
        prefix = pattern.rstrip("*")
        return [k for k in self.kv if k.startswith(prefix)]


class FakeCache:
    """CacheStore whose ``_client`` is a native redis-like client."""

    def __init__(self) -> None:
        self._client = FakeRedis()


def _store():
    return SessionStore(FakeCache())


def test_sync_round_trip():
    store = _store()
    ctx = SessionContext(session_id="s1", primary_entity_id="device-9")
    store.save(ctx)
    loaded = store.load("s1")
    assert loaded is not None
    assert loaded.session_id == "s1"
    assert loaded.primary_entity_id == "device-9"


def test_list_active_returns_all_sessions():
    store = _store()
    store.save(SessionContext(session_id="a"))
    store.save(SessionContext(session_id="b"))
    ids = {c.session_id for c in store.list_active()}
    assert ids == {"a", "b"}


def test_corrupt_record_is_skipped_with_warning(caplog):
    store = _store()
    store.save(SessionContext(session_id="good"))
    # Inject a record that is not valid JSON.
    store._cache._client.kv["session:broken"] = "{not-json"
    with caplog.at_level("WARNING"):
        active = store.list_active()
    assert {c.session_id for c in active} == {"good"}
    assert any("unreadable session record" in r.message for r in caplog.records)


async def test_async_round_trip():
    store = _store()
    ctx = SessionContext(session_id="async-1")
    await store.asave(ctx)
    loaded = await store.aload("async-1")
    assert loaded is not None and loaded.session_id == "async-1"
    active = await store.alist_active()
    assert {c.session_id for c in active} == {"async-1"}
