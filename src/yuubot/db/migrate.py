from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_PATTERN = re.compile(r"^(\d+)_.*\.sql$")
_PROVIDER_CONTROL_PLANE_DROP_VERSION = 10


def migration_files() -> list[tuple[int, Path]]:
    items: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_PATTERN.match(path.name)
        if match is None:
            raise ValueError(f"invalid migration filename: {path.name}")
        items.append((int(match.group(1)), path))
    return items


async def current_version(db: Database) -> int:
    cursor = await db.execute(
        "select name from sqlite_master where type = 'table' and name = 'app_meta'"
    )
    if await cursor.fetchone() is None:
        return 0
    cursor = await db.execute("select value from app_meta where key = 'schema_version'")
    row = await cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def pending_versions(db: Database) -> list[int]:
    applied = await current_version(db)
    return [version for version, _path in migration_files() if version > applied]


async def migrate(db: Database) -> int:
    applied = await current_version(db)
    for version, path in migration_files():
        if version <= applied:
            continue
        sql = path.read_text(encoding="utf-8")
        await db.executescript(sql)
        await db.execute(
            "insert or replace into app_meta (key, value) values ('schema_version', ?)",
            (str(version),),
        )
        await db.commit()
        if version == _PROVIDER_CONTROL_PLANE_DROP_VERSION:
            # The dropped provider rows may have contained credentials. A
            # secure-delete rewrite plus WAL truncation ensures old frames do
            # not remain in SQLite sidecar files after the migration commits.
            cursor = await db.execute("pragma wal_checkpoint(truncate)")
            await cursor.fetchall()
        applied = version
    return applied
