"""Immutable provenance log — append-only, hash-chained MongoDB capped collection."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo import MongoClient

log = logging.getLogger(__name__)

_COLLECTION = "provenance_log"
_GENESIS_HASH = "0" * 64


class ProvenanceLog:
    """Append-only audit log with a true hash chain.

    Each entry stores its own SHA-256 ``entry_hash`` computed over
    ``prev_hash || canonical(entry_fields)``. Tampering with any past record
    breaks the chain at the next record, so the log is tamper-evident provided
    the latest ``entry_hash`` is replicated outside the database.
    """

    def __init__(self, mongo_client: MongoClient, db_name: str = "digital_twin",
                 cap_bytes: int = 256 * 1024 * 1024) -> None:
        db = mongo_client[db_name]
        # Create capped collection if it doesn't exist
        existing = db.list_collection_names()
        if _COLLECTION not in existing:
            db.create_collection(_COLLECTION, capped=True, size=cap_bytes)
        self._col = db[_COLLECTION]
        self._lock = threading.Lock()
        # Seed the chain head from the most recently inserted entry so the chain
        # survives process restarts. Capped collections preserve insertion order
        # in natural order, so $natural is exact — unlike a timestamp sort, which
        # can tie (and reorder) under a high write rate.
        latest = self._col.find_one({}, sort=[("$natural", -1)])
        self._last_id: str | None = latest["event_id"] if latest else None
        self._last_hash: str = latest.get("entry_hash", _GENESIS_HASH) if latest else _GENESIS_HASH

    @staticmethod
    def _canonical(data: Any) -> bytes:
        return json.dumps(data, sort_keys=True, default=str).encode()

    @classmethod
    def _hash_input(cls, data: Any) -> str:
        return hashlib.sha256(cls._canonical(data)).hexdigest()

    @classmethod
    def _link_hash(cls, prev_hash: str, entry_body: dict) -> str:
        h = hashlib.sha256()
        h.update(prev_hash.encode())
        h.update(cls._canonical(entry_body))
        return h.hexdigest()

    def append(self, actor: str, inputs: Any, output_summary: str,
               model_version: str = "unknown", session_id: str = "") -> str:
        with self._lock:
            event_id = str(uuid.uuid4())
            body = {
                "event_id": event_id,
                "timestamp": time.time(),
                "actor": actor,
                "input_hash": self._hash_input(inputs),
                "output_summary": output_summary,
                "model_version": model_version,
                "session_id": session_id,
                "prev_id": self._last_id,
                "prev_hash": self._last_hash,
            }
            entry_hash = self._link_hash(self._last_hash, body)
            body["entry_hash"] = entry_hash
            self._col.insert_one(body)
            self._last_id = event_id
            self._last_hash = entry_hash
            return event_id

    def verify_chain(self) -> bool:
        """Walk the log in insertion order and check every link.

        Returns True iff every retained entry's ``entry_hash`` matches the
        recomputed value over its own ``prev_hash + body`` and each entry chains
        to the one before it (``prev_hash``/``prev_id`` pointers).

        The collection is **capped**: once it fills, the oldest entries are
        evicted, so the oldest *retained* entry's ``prev_hash`` legitimately no
        longer points at the genesis hash. We therefore anchor on that first
        retained entry — trusting its self-consistent hash — and verify the chain
        forward from there, rather than insisting the head reaches genesis (which
        would make verification fail permanently after the first rotation).

        Natural order is exact insertion order for a capped collection, so we do
        not sort on the float ``timestamp`` (which can tie and reorder).
        """
        prev_hash: str | None = None
        prev_id: str | None = None
        for entry in self._col.find({}, {"_id": 0}):
            stored = entry.get("entry_hash")
            if stored is None:
                return False
            body = {k: v for k, v in entry.items() if k != "entry_hash"}
            # Integrity: the entry's hash must match its own stored prev_hash+body
            # (catches any tampering with the record's contents).
            if self._link_hash(entry.get("prev_hash"), body) != stored:
                return False
            # Linkage: every entry after the anchor must point at its predecessor.
            if prev_hash is not None and (
                entry.get("prev_hash") != prev_hash or entry.get("prev_id") != prev_id
            ):
                return False
            prev_hash = stored
            prev_id = entry.get("event_id")
        return True

    def query_by_session(self, session_id: str) -> list[dict]:
        return list(self._col.find({"session_id": session_id}, {"_id": 0}))

    def query_recent(self, limit: int = 100) -> list[dict]:
        return list(self._col.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
