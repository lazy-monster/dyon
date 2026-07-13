"""Regression tests for the KnowledgeGraph constructor guard (assessment §2.1).

A twin once passed a KnowledgeGraphSpec where a Neo4j driver was expected, which
silently disabled all diagnostics. The constructor now rejects anything that is
not a driver.
"""

from __future__ import annotations

import pytest

from dyon.intelligent.knowledge_graph import KnowledgeGraph, KnowledgeGraphSpec


def _spec():
    return KnowledgeGraphSpec(components=[], failure_modes=[], symptom_mappings=[])


def test_rejects_spec_passed_as_driver():
    with pytest.raises(TypeError, match="not a KnowledgeGraphSpec"):
        KnowledgeGraph(config=None, driver=_spec())


def test_rejects_non_driver_object():
    with pytest.raises(TypeError, match="Neo4j driver"):
        KnowledgeGraph(config=None, driver=object())


def test_accepts_driver_like_object():
    class FakeDriver:
        def session(self):
            raise NotImplementedError

    kg = KnowledgeGraph(config=None, driver=FakeDriver())
    assert kg.driver is not None
    # Spec is only attached once setup_from_spec runs.
    assert kg._spec is None
