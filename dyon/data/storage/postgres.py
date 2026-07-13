"""PostgreSQL adapter using asyncpg for relational storage."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class PostgresAdapter:
    """Thin asyncpg wrapper — connection pooling, query helpers."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None

    async def connect(self) -> None:
        from dyon._compat import require
        asyncpg = require("asyncpg", "stores")
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=self._min_size, max_size=self._max_size
        )
        log.info("PostgresAdapter connected (pool size %d–%d)", self._min_size, self._max_size)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def _require_pool(self) -> None:
        if not self._pool:
            raise RuntimeError("PostgresAdapter: call connect() first")

    async def execute(self, query: str, *args: Any) -> str:
        self._require_pool()
        return await self._pool.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        self._require_pool()
        rows = await self._pool.fetch(query, *args)
        return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        self._require_pool()
        row = await self._pool.fetchrow(query, *args)
        return dict(row) if row else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._require_pool()
        return await self._pool.fetchval(query, *args)
