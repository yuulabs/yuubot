from __future__ import annotations

import json
import importlib.metadata
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qs, unquote, urlsplit

from yuubot.config import Config
from yuubot.core.db import _find_libsimple

Category = Literal["core", "messages", "traces"]
SUPPORTED_CATEGORIES: tuple[Category, ...] = ("core", "messages", "traces")
MANIFEST_VERSION = 1
_MESSAGE_TABLES = ("contexts", "messages", "forwards")
_SQLITE_INSERT_RE = re.compile(r'^INSERT INTO "?([^"\s]+)"?\s')
_FTS_TABLE_RE = re.compile(r"_fts($|_)")


@dataclass(frozen=True)
class CategoryEntry:
    metadata_path: str
    payload_paths: list[str]


@dataclass(frozen=True)
class ArchiveManifest:
    manifest_version: int
    created_at: str
    source: dict[str, str]
    categories: list[Category]
    entries: dict[Category, CategoryEntry]


def parse_categories(parts: tuple[str, ...] | list[str]) -> tuple[Category, ...]:
    tokens: list[str] = []
    for part in parts:
        for plus_part in part.split("+"):
            for comma_part in plus_part.split(","):
                token = comma_part.strip().lower()
                if token:
                    tokens.append(token)

    if not tokens:
        return SUPPORTED_CATEGORIES

    ordered: list[Category] = []
    for token in tokens:
        if token not in SUPPORTED_CATEGORIES:
            expected = ", ".join(SUPPORTED_CATEGORIES)
            raise ValueError(f"unsupported category {token!r}; expected one of: {expected}")
        if token not in ordered:
            ordered.append(cast(Category, token))
    return tuple(ordered)


def _exportable_path(raw_path: str) -> Path | None:
    text = str(raw_path).strip()
    if not text:
        return None
    if text.startswith("file:"):
        parsed = urlsplit(text)
        query = parse_qs(parsed.query)
        if query.get("mode") == ["memory"]:
            return None
        if not parsed.path:
            return None
        return Path(unquote(parsed.path)).expanduser()
    return Path(text).expanduser()


def _trace_db_path(cfg: Config) -> Path | None:
    tracing_cfg = cfg.yuuagents.get("yuutrace") or {}
    db_path = tracing_cfg.get("db_path") or "~/.yagents/traces.db"
    return _exportable_path(str(db_path))


def _deployment_mode() -> str:
    explicit = str(os.environ.get("YUU_DEPLOYMENT_MODE", "") or "").strip()
    if explicit in {"bare_machine", "container"}:
        return explicit
    return "container" if Path("/.dockerenv").exists() else "bare_machine"


def _source_version(cfg: Config) -> dict[str, str]:
    del cfg

    def _version(package: str) -> str:
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    return {
        "product": "yuubot",
        "yuubot_version": _version("yuubot"),
        "yuuagents_version": _version("yuuagents"),
        "deployment_mode": _deployment_mode(),
    }


def _sqlite_user_tables(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    return [str(name) for (name,) in rows if not _FTS_TABLE_RE.search(str(name))]


def _normalize_schema_sql(sql: str, kind: str) -> str:
    if kind == "table":
        return re.sub(r"^CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", sql, count=1)
    if kind == "index":
        return re.sub(r"^CREATE( UNIQUE)? INDEX ", r"CREATE\1 INDEX IF NOT EXISTS ", sql, count=1)
    return sql


def _normalize_insert_sql(sql: str) -> str:
    return sql.replace("INSERT INTO", "INSERT OR REPLACE INTO", 1)


def _dump_selected_tables(db_path: Path, tables: list[str]) -> str:
    if not tables:
        return "PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\nCOMMIT;\n"

    table_set = set(tables)
    with sqlite3.connect(db_path) as conn:
        script = ["PRAGMA foreign_keys=OFF;", "BEGIN TRANSACTION;"]
        for kind, _name, tbl_name, sql in conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name
            """
        ):
            if tbl_name in table_set and sql:
                script.append(f"{_normalize_schema_sql(str(sql), str(kind))};")

        for line in conn.iterdump():
            match = _SQLITE_INSERT_RE.match(line)
            if match and match.group(1) in table_set:
                script.append(f"{_normalize_insert_sql(line)};")
        script.append("COMMIT;")
    return "\n".join(script) + "\n"


def _core_tables(db_path: Path) -> list[str]:
    return [name for name in _sqlite_user_tables(db_path) if name not in _MESSAGE_TABLES]


def _messages_tables(db_path: Path) -> list[str]:
    existing = set(_sqlite_user_tables(db_path))
    return [name for name in _MESSAGE_TABLES if name in existing]


def _directory_exists(path: Path) -> bool:
    return path.exists() and path.is_dir()


def _build_manifest(cfg: Config, categories: tuple[Category, ...]) -> ArchiveManifest:
    entries: dict[Category, CategoryEntry] = {}
    db_path = Path(cfg.database.path).expanduser()
    browser_profile = Path(cfg.web.browser_profile).expanduser()
    downloads = Path(cfg.web.download_dir).expanduser()
    media_dir = Path(cfg.recorder.media_dir).expanduser()
    traces_db = _trace_db_path(cfg)

    for category in categories:
        payload_paths: list[str] = []
        if category == "core":
            if db_path.exists() and _core_tables(db_path):
                payload_paths.append("core/db/core.sql")
            if _directory_exists(browser_profile):
                payload_paths.append("core/browser_profile")
            if _directory_exists(downloads):
                payload_paths.append("core/downloads")
        elif category == "messages":
            if db_path.exists() and _messages_tables(db_path):
                payload_paths.append("messages/db/messages.sql")
            if _directory_exists(media_dir):
                payload_paths.append("messages/media")
        elif category == "traces":
            if traces_db is not None and traces_db.exists():
                payload_paths.append("traces/traces.db")
        entries[category] = CategoryEntry(
            metadata_path=f"{category}/metadata.json",
            payload_paths=payload_paths,
        )

    return ArchiveManifest(
        manifest_version=MANIFEST_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        source=_source_version(cfg),
        categories=list(categories),
        entries=entries,
    )


def _write_path_to_zip(
    zf: zipfile.ZipFile,
    source: Path,
    archive_path: str,
) -> None:
    if source.is_file():
        zf.write(source, archive_path)
        return

    for child in sorted(source.rglob("*")):
        if child.is_dir():
            continue
        rel = child.relative_to(source).as_posix()
        try:
            zf.write(child, f"{archive_path}/{rel}")
        except FileNotFoundError:
            continue


def export_archive(cfg: Config, output_path: str | Path, categories: tuple[Category, ...]) -> ArchiveManifest:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest = _build_manifest(cfg, categories)
    db_path = Path(cfg.database.path).expanduser()
    browser_profile = Path(cfg.web.browser_profile).expanduser()
    downloads = Path(cfg.web.download_dir).expanduser()
    media_dir = Path(cfg.recorder.media_dir).expanduser()
    traces_db = _trace_db_path(cfg)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(asdict(manifest), ensure_ascii=False, indent=2))

        for category in manifest.categories:
            entry = manifest.entries[category]
            zf.writestr(
                entry.metadata_path,
                json.dumps(
                    {
                        "category": category,
                        "payload_paths": entry.payload_paths,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

            if category == "core":
                if "core/db/core.sql" in entry.payload_paths:
                    zf.writestr("core/db/core.sql", _dump_selected_tables(db_path, _core_tables(db_path)))
                if "core/browser_profile" in entry.payload_paths:
                    _write_path_to_zip(zf, browser_profile, "core/browser_profile")
                if "core/downloads" in entry.payload_paths:
                    _write_path_to_zip(zf, downloads, "core/downloads")
            elif category == "messages":
                if "messages/db/messages.sql" in entry.payload_paths:
                    zf.writestr(
                        "messages/db/messages.sql",
                        _dump_selected_tables(db_path, _messages_tables(db_path)),
                    )
                if "messages/media" in entry.payload_paths:
                    _write_path_to_zip(zf, media_dir, "messages/media")
            elif category == "traces" and traces_db is not None and "traces/traces.db" in entry.payload_paths:
                _write_path_to_zip(zf, traces_db, "traces/traces.db")
    return manifest


def _read_manifest(archive_path: str | Path) -> ArchiveManifest:
    with zipfile.ZipFile(archive_path, "r") as zf:
        raw = json.loads(zf.read("manifest.json"))
    entries = {
        cast(Category, category): CategoryEntry(**entry)
        for category, entry in raw["entries"].items()
    }
    return ArchiveManifest(
        manifest_version=int(raw["manifest_version"]),
        created_at=str(raw["created_at"]),
        source=dict(raw["source"]),
        categories=list(raw["categories"]),
        entries=entries,
    )


def _restore_target(cfg: Config, key: str) -> Path:
    mapping = {
        "database": Path(cfg.database.path).expanduser(),
        "browser_profile": Path(cfg.web.browser_profile).expanduser(),
        "downloads": Path(cfg.web.download_dir).expanduser(),
        "media": Path(cfg.recorder.media_dir).expanduser(),
        "traces_db": _trace_db_path(cfg),
    }
    target = mapping[key]
    if target is None:
        raise ValueError(f"no restore target configured for {key!r}")
    return target


def _apply_sql_payload(target_db: Path, sql_text: str) -> None:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target_db) as conn:
        path = _find_libsimple()
        if path:
            try:
                conn.enable_load_extension(True)
                conn.load_extension(path)
                conn.enable_load_extension(False)
                dict_dir = Path(path).parent / "dict"
                if dict_dir.is_dir():
                    conn.execute("SELECT jieba_dict(?)", [str(dict_dir)])
            except sqlite3.Error:
                conn.enable_load_extension(False)
        conn.executescript(sql_text)
        conn.commit()


def _restore_directory(zf: zipfile.ZipFile, prefix: str, dest_root: Path) -> None:
    dest_root.mkdir(parents=True, exist_ok=True)
    for member in zf.namelist():
        if not member.startswith(prefix) or member.endswith("/"):
            continue
        rel = Path(member.removeprefix(prefix))
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)


def import_archive(
    cfg: Config,
    archive_path: str | Path,
    *,
    categories: tuple[Category, ...] | None = None,
    dry_run: bool = False,
) -> ArchiveManifest:
    manifest = _read_manifest(archive_path)
    if manifest.manifest_version != MANIFEST_VERSION:
        raise ValueError(
            f"unsupported manifest version {manifest.manifest_version}; expected {MANIFEST_VERSION}"
        )

    selected = categories or tuple(manifest.categories)
    if not set(selected).issubset(set(manifest.categories)):
        raise ValueError("requested import categories are not all present in the archive")

    if dry_run:
        return manifest

    with zipfile.ZipFile(archive_path, "r") as zf:
        for category in selected:
            entry = manifest.entries[category]
            for payload_path in entry.payload_paths:
                if payload_path == "core/db/core.sql" or payload_path == "messages/db/messages.sql":
                    _apply_sql_payload(
                        _restore_target(cfg, "database"),
                        zf.read(payload_path).decode("utf-8"),
                    )
                    continue
                if payload_path == "core/browser_profile":
                    _restore_directory(zf, "core/browser_profile/", _restore_target(cfg, "browser_profile"))
                    continue
                if payload_path == "core/downloads":
                    _restore_directory(zf, "core/downloads/", _restore_target(cfg, "downloads"))
                    continue
                if payload_path == "messages/media":
                    _restore_directory(zf, "messages/media/", _restore_target(cfg, "media"))
                    continue
                if payload_path == "traces/traces.db":
                    dest = _restore_target(cfg, "traces_db")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(payload_path) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
    return manifest
