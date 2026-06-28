"""Async-native Tortoise ORM database wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tortoise.context import TortoiseContext, _current_context
from tortoise.exceptions import OperationalError
from tortoise.transactions import in_transaction


@dataclass
class DB:
    """Async-native wrapper around the Tortoise ORM runtime."""

    path: str
    _ctx: TortoiseContext = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    async def open(cls, path: str) -> DB:
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        ctx = TortoiseContext()
        db = cls(path=path, _ctx=ctx)
        with db.activate():
            await db._init_orm()
        return db

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._close_orm()

    @contextmanager
    def activate(self) -> Iterator[None]:
        token = _current_context.set(self._ctx)
        try:
            yield
        finally:
            _current_context.reset(token)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        with self.activate():
            async with in_transaction():
                yield

    async def migrate(self) -> None:
        with self.activate():
            await self._migrate_orm()

    async def _init_orm(self) -> None:
        await self._ctx.init(
            db_url=_sqlite_url(self.path),
            modules={"models": ["yuubot.resources.store.models"]},
            use_tz=False,
            timezone="UTC",
            _create_db=True,
        )

    async def _migrate_orm(self) -> None:
        await self._ctx.generate_schemas(safe=True)
        await self._ensure_actor_skill_scope_column()

    async def _ensure_actor_skill_scope_column(self) -> None:
        connection_name = self._ctx.default_connection
        if connection_name is None:
            raise RuntimeError("database default connection is not initialized")
        connection = self._ctx.connections.get(connection_name)
        try:
            await connection.execute_script(
                "ALTER TABLE actors ADD COLUMN skill_scope VARCHAR(255) "
                "NOT NULL DEFAULT 'global_and_local';"
            )
        except OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    async def _close_orm(self) -> None:
        await self._ctx.close_connections()


def _sqlite_url(path: str) -> str:
    if path == ":memory:":
        return "sqlite://:memory:"
    return f"sqlite://{Path(path).expanduser()}"
