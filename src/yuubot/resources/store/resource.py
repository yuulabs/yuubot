"""Resource persistence boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from yuubot.resources.store.base import DB


@dataclass
class Store:
    """Thin wrapper over DB providing transaction scope and lifecycle."""

    db: DB

    @classmethod
    async def open(cls, path: str) -> Store:
        db = await DB.open(path)
        return cls(db=db)

    async def close(self) -> None:
        await self.db.close()

    async def migrate(self) -> None:
        await self.db.migrate()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self.db.transaction():
            yield


__all__ = ["Store"]
