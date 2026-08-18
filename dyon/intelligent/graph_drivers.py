"""Graph drivers for runs without Neo4j.

:class:`~dyon.intelligent.knowledge_graph.KnowledgeGraph` takes a Neo4j driver
and talks Cypher to it. That is the right dependency for a deployed twin, but it
means a twin with no database reachable spends its startup logging a warning per
schema statement, and every later query falls through the defensive handlers one
timeout at a time.

:class:`NullGraphDriver` makes the absence explicit instead. It satisfies the
driver contract — ``session()`` as a context manager, ``run()`` returning an
iterable result — and answers everything with nothing. Schema setup becomes a
no-op, queries return empty, and the calling code takes the fallback path it
already has for an unreachable graph, quietly and immediately.

::

    from dyon.intelligent import KnowledgeGraph, NullGraphDriver

    kg = KnowledgeGraph(config, NullGraphDriver())
    kg.setup_from_spec(spec)     # accepted, stored, nothing written

Use it when the graph is genuinely optional: a demo, a test, an edge node whose
diagnostics run off thresholds alone. A twin whose reasoning depends on graph
answers should point at a real database — an empty result is a truthful "I do
not know", not a substitute for one.

Note that :meth:`KnowledgeGraph.diagnose_from_readings` keeps working either
way: it evaluates the spec's symptom mappings in Python and never touches the
driver, so threshold-driven symptom detection survives the graph being absent.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any

log = logging.getLogger(__name__)


class NullGraphResult:
    """An empty Cypher result: iterable, countable, and safely consumable."""

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def single(self) -> None:
        return None

    def data(self) -> list[dict]:
        return []

    def consume(self) -> None:
        return None


class NullGraphSession:
    """A session that accepts Cypher and returns :class:`NullGraphResult`."""

    def __init__(self, record_statements: bool = False) -> None:
        self._record = record_statements
        self.statements: list[tuple[str, dict]] = []

    def run(self, query: str, parameters: dict | None = None, **kwargs: Any):
        if self._record:
            self.statements.append((query, {**(parameters or {}), **kwargs}))
        return NullGraphResult()

    def close(self) -> None:
        return None

    def __enter__(self) -> NullGraphSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class NullGraphDriver:
    """A Neo4j-shaped driver that stores nothing and answers nothing.

    Pass ``record_statements=True`` to keep the Cypher it was handed. Nothing in
    the framework reads that back; it exists so a test can assert which schema a
    spec would have written without standing up a database.
    """

    def __init__(self, record_statements: bool = False) -> None:
        self._record = record_statements
        self.sessions: list[NullGraphSession] = []

    def session(self, **kwargs: Any) -> NullGraphSession:
        session = NullGraphSession(record_statements=self._record)
        if self._record:
            self.sessions.append(session)
        return session

    def statements(self) -> list[tuple[str, dict]]:
        """Every statement seen across sessions, in order.

        Empty unless the driver was built with ``record_statements=True``.
        """
        return [stmt for session in self.sessions for stmt in session.statements]

    def verify_connectivity(self) -> None:
        return None

    def close(self) -> None:
        return None


__all__ = ["NullGraphDriver", "NullGraphResult", "NullGraphSession"]
