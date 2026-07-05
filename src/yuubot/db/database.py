from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from .migrate import migrate


class Database:
    def __init__(self, connection: aiosqlite.Connection, path: Path) -> None:
        self._connection = connection
        self._path = path
        self._closed = False
        self._transaction_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    async def open(cls, db_dir: str | Path, *, migrate_on_open: bool = True) -> Database:
        directory = Path(db_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "yuubot.db"
        connection = await aiosqlite.connect(path)
        await connection.execute("pragma journal_mode = wal")
        await connection.execute("pragma foreign_keys = on")
        await connection.execute("pragma busy_timeout = 5000")
        db = cls(connection, path)
        if migrate_on_open:
            await migrate(db)
        return db

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._connection.close()

    async def execute(self, sql: str, parameters: tuple[Any, ...] | list[Any] = ()) -> aiosqlite.Cursor:
        return await self._connection.execute(sql, parameters)

    async def executemany(self, sql: str, parameters: list[tuple[Any, ...]]) -> aiosqlite.Cursor:
        return await self._connection.executemany(sql, parameters)

    async def executescript(self, sql: str) -> None:
        await self._connection.executescript(sql)

    async def commit(self) -> None:
        await self._connection.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Database]:
        async with self._transaction_lock:
            await self._connection.execute("begin immediate")
            try:
                yield self
                await self._connection.commit()
            except BaseException:
                await self._connection.rollback()
                raise
