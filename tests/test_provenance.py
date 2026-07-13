"""Regression tests for ProvenanceLog hash-chain verification (assessment §2.3).

The log lives in a capped collection, so the oldest entries are evicted over
time. verify_chain() must therefore anchor on the oldest *retained* entry
instead of insisting the chain reaches the genesis hash (which broke
verification permanently after the first rotation), while still detecting any
tampering with a retained record.
"""

from __future__ import annotations

from conftest import FakeMongoClient

from dyon.data.storage.provenance import ProvenanceLog


def _seed(log: ProvenanceLog, n: int) -> None:
    for i in range(n):
        log.append(actor=f"actor{i}", inputs={"i": i}, output_summary=f"out{i}")


def test_full_chain_verifies():
    client = FakeMongoClient()
    log = ProvenanceLog(client)
    _seed(log, 6)
    assert log.verify_chain() is True


def test_chain_survives_capped_eviction():
    client = FakeMongoClient()
    log = ProvenanceLog(client)
    _seed(log, 6)
    # Simulate the capped collection evicting the two oldest entries.
    col = client["digital_twin"]["provenance_log"]
    col.docs = col.docs[2:]
    assert log.verify_chain() is True


def test_tampering_is_detected():
    client = FakeMongoClient()
    log = ProvenanceLog(client)
    _seed(log, 6)
    col = client["digital_twin"]["provenance_log"]
    col.docs[3]["output_summary"] = "tampered"
    assert log.verify_chain() is False


def test_broken_link_is_detected():
    client = FakeMongoClient()
    log = ProvenanceLog(client)
    _seed(log, 4)
    col = client["digital_twin"]["provenance_log"]
    # Corrupt a prev_hash pointer without touching the entry's own hash input
    # would still be caught, so instead drop a middle entry to break linkage.
    del col.docs[2]
    assert log.verify_chain() is False
