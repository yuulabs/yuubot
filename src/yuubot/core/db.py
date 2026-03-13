"""Tortoise ORM connection management and schema initialization."""

import glob
from pathlib import Path

from tortoise import Tortoise, connections

from loguru import logger

# ── FTS5 schemas ─────────────────────────────────────────────────
# Messages FTS uses default tokenizer (mostly ASCII/mixed content).
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
"""

# Memories FTS uses simple tokenizer for Chinese support when available,
# falls back to default tokenizer otherwise.
_MEMORY_FTS_SIMPLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='simple'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
"""

_MEMORY_FTS_DEFAULT = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
"""

_IMAGES_FTS_SIMPLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
    description,
    content='images',
    content_rowid='id',
    tokenize='simple'
);

CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
    INSERT INTO images_fts(rowid, description) VALUES (new.id, new.description);
END;

CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
    INSERT INTO images_fts(images_fts, rowid, description)
    VALUES ('delete', old.id, old.description);
END;

CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE OF description ON images BEGIN
    INSERT INTO images_fts(images_fts, rowid, description)
    VALUES ('delete', old.id, old.description);
    INSERT INTO images_fts(rowid, description) VALUES (new.id, new.description);
END;
"""

_IMAGES_FTS_DEFAULT = """
CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
    description,
    content='images',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
    INSERT INTO images_fts(rowid, description) VALUES (new.id, new.description);
END;

CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
    INSERT INTO images_fts(images_fts, rowid, description)
    VALUES ('delete', old.id, old.description);
END;

CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE OF description ON images BEGIN
    INSERT INTO images_fts(images_fts, rowid, description)
    VALUES ('delete', old.id, old.description);
    INSERT INTO images_fts(rowid, description) VALUES (new.id, new.description);
END;
"""

SEED_SQL = "INSERT OR IGNORE INTO memory_config (key, value) VALUES ('forget_days', '90');"

# Rebuild FTS index from source table after tokenizer change.
_REBUILD_MEMORY_FTS = """
INSERT INTO memories_fts(memories_fts) VALUES ('rebuild');
"""

# ── Schema migrations ────────────────────────────────────────────
# Each entry: (check_sql, migrate_sql)
# check_sql returns rows if migration is NOT needed.
_MIGRATIONS = [
    # v0.2: add source_user_id to memories
    (
        "SELECT 1 FROM pragma_table_info('memories') WHERE name='source_user_id'",
        "ALTER TABLE memories ADD COLUMN source_user_id BIGINT",
    ),
    # v0.3: add index on messages.message_id for OneBot message_id lookups
    (
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_messages_message_id'",
        "CREATE INDEX idx_messages_message_id ON messages(message_id)",
    ),
    # v0.4: add scope to memories for context isolation
    (
        "SELECT 1 FROM pragma_table_info('memories') WHERE name='scope'",
        "ALTER TABLE memories ADD COLUMN scope VARCHAR(16) DEFAULT 'private'",
    ),
    # v0.5: add trashed_at for soft-delete (trash bin)
    (
        "SELECT 1 FROM pragma_table_info('memories') WHERE name='trashed_at'",
        "ALTER TABLE memories ADD COLUMN trashed_at TIMESTAMP NULL",
    ),
]

# Post-migration: set scope=public for memories with NULL ctx_id
_SCOPE_MIGRATE_SQL = "UPDATE memories SET scope = 'public' WHERE ctx_id IS NULL AND scope = 'private';"

# Runtime flag: True when simple tokenizer is loaded.
_simple_loaded = False
# Set True when memories_fts was dropped and needs rebuild after recreation.
_fts_rebuilt = False


def _find_libsimple() -> str | None:
    """Auto-detect libsimple under vendor/ relative to project root."""
    project_root = Path(__file__).resolve().parents[3]  # src/yuubot/core -> project root
    patterns = [
        str(project_root / "vendor" / "*" / "libsimple"),
        str(project_root / "vendor" / "libsimple"),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern + ".so") + glob.glob(pattern)
        for m in matches:
            # strip .so for load_extension
            return m.removesuffix(".so")
    return None


async def _load_simple_ext(conn, ext_path: str = "") -> bool:
    """Load libsimple SQLite extension and init jieba dict. Returns True on success."""
    global _simple_loaded
    path = ext_path or _find_libsimple()
    if not path:
        logger.info("libsimple not found, using default FTS5 tokenizer")
        return False
    try:
        raw_conn = conn._connection
        await raw_conn.enable_load_extension(True)
        await raw_conn.load_extension(path)
        await raw_conn.enable_load_extension(False)
        _simple_loaded = True
        logger.info("Loaded libsimple from {}", path)

        # Init jieba dict — look for dict/ next to libsimple binary
        dict_dir = Path(path).parent / "dict"
        if dict_dir.is_dir():
            await conn.execute_query("SELECT jieba_dict(?)", [str(dict_dir)])
            logger.info("Loaded jieba dict from {}", dict_dir)

        return True
    except Exception:
        logger.opt(exception=True).warning("Failed to load libsimple from {}, using default tokenizer", path)
        return False


def has_simple() -> bool:
    """Return True if simple tokenizer is available in current session."""
    return _simple_loaded


async def _migrate_memory_fts(conn, simple_available: bool) -> None:
    """Drop memories_fts if its tokenizer doesn't match the desired one.

    When simple is available but the existing table uses default (or vice versa),
    we must drop and let init_db recreate it with the correct tokenizer.
    """
    global _fts_rebuilt
    _fts_rebuilt = False

    # Check if memories_fts exists
    _, rows = await conn.execute_query(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    )
    if not rows:
        # Table doesn't exist yet — will be created fresh, needs rebuild
        _fts_rebuilt = True
        return

    current_sql = rows[0][0] if rows[0] else ""
    has_simple_tokenizer = "simple" in current_sql.lower()

    if simple_available == has_simple_tokenizer:
        return  # tokenizer matches, nothing to do

    logger.info(
        "Migrating memories_fts: %s → %s tokenizer",
        "simple" if has_simple_tokenizer else "default",
        "simple" if simple_available else "default",
    )
    await conn.execute_script(
        "DROP TRIGGER IF EXISTS memories_ai;"
        "DROP TRIGGER IF EXISTS memories_ad;"
        "DROP TABLE IF EXISTS memories_fts;"
    )
    _fts_rebuilt = True


async def init_db(db_path: str, *, simple_ext: str = "") -> None:
    """Init Tortoise ORM, create schema, enable WAL, setup FTS5.

    Args:
        db_path: SQLite database file path.
        simple_ext: Path to libsimple extension (without .so suffix).
            Empty string triggers auto-detection under vendor/.
    """
    import inspect
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    caller_loc = f"{caller.f_code.co_filename}:{caller.f_lineno}" if caller else "?"
    logger.debug("init_db from {}", caller_loc)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await Tortoise.init(
        db_url=f"sqlite://{db_path}",
        modules={"models": ["yuubot.core.models"]},
        _enable_global_fallback=True,
    )
    await Tortoise.generate_schemas()

    conn = connections.get("default")
    await conn.execute_query("PRAGMA journal_mode=WAL")
    await conn.execute_query("PRAGMA foreign_keys=ON")

    # Run schema migrations for existing databases
    for check_sql, migrate_sql in _MIGRATIONS:
        _, rows = await conn.execute_query(check_sql)
        if not rows:
            logger.info("Running migration: {}", migrate_sql[:60])
            await conn.execute_query(migrate_sql)

    # Load simple tokenizer for Chinese FTS5 support
    has = await _load_simple_ext(conn, simple_ext)
    memory_fts_sql = _MEMORY_FTS_SIMPLE if has else _MEMORY_FTS_DEFAULT

    # Migrate memories_fts tokenizer: drop old table if tokenizer changed
    await _migrate_memory_fts(conn, has)

    images_fts_sql = _IMAGES_FTS_SIMPLE if has else _IMAGES_FTS_DEFAULT

    await conn.execute_script(FTS_SQL)
    await conn.execute_script(memory_fts_sql)
    await conn.execute_script(images_fts_sql)

    # Rebuild FTS index if table was just (re)created with new tokenizer
    if _fts_rebuilt:
        logger.info("Rebuilding memories_fts index after tokenizer change")
        await conn.execute_script(_REBUILD_MEMORY_FTS)

    await conn.execute_script(SEED_SQL)

    # Migrate existing NULL-ctx memories to public scope
    await conn.execute_query(_SCOPE_MIGRATE_SQL)


async def close_db() -> None:
    """Close all Tortoise connections and reset global context."""
    import inspect
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    caller_loc = f"{caller.f_code.co_filename}:{caller.f_lineno}" if caller else "?"
    logger.warning("close_db from {} (destroys Tortoise context!)", caller_loc)

    global _simple_loaded, _fts_rebuilt
    _simple_loaded = False
    _fts_rebuilt = False
    ctx = Tortoise._get_context()
    if ctx is not None:
        await ctx.close_connections()
    else:
        await Tortoise.close_connections()
