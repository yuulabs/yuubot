"""Legacy database import entry point."""

from pathlib import Path

from ..db import Database, auto_legacy_db, migrate_legacy


async def maybe_import_legacy(data_dir: Path, db: Database) -> None:
    db_path = data_dir / "db" / "yuubot.db"
    legacy_db = auto_legacy_db(data_dir) if not db_path.exists() else None
    if legacy_db is not None:
        await migrate_legacy(db, data_dir=data_dir, legacy_db=legacy_db)
