from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from yuubot.config import (
    BotConfig,
    Config,
    DaemonConfig,
    DatabaseConfig,
    RecorderConfig,
    ResponseConfig,
    SessionConfig,
    WebConfig,
)
from yuubot.portability import export_archive, import_archive, parse_categories


def _seed_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE contexts (
                id INTEGER PRIMARY KEY,
                type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                created_at TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                message_id INTEGER,
                ctx_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                nickname TEXT,
                display_name TEXT,
                content TEXT NOT NULL,
                raw_message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                media_files TEXT NOT NULL
            );
            CREATE TABLE forwards (
                id INTEGER PRIMARY KEY,
                forward_id TEXT UNIQUE,
                summary TEXT,
                raw_nodes TEXT,
                source_message_id INTEGER,
                source_ctx_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content,
                content='messages',
                content_rowid='id'
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                content,
                content='memories',
                content_rowid='id'
            );
            INSERT INTO contexts (id, type, target_id, created_at) VALUES (1, 'group', 42, '2026-04-19T00:00:00Z');
            INSERT INTO messages (id, message_id, ctx_id, user_id, nickname, display_name, content, raw_message, timestamp, media_files)
            VALUES (1, 1001, 1, 2001, 'nick', 'disp', 'hello', '[]', '2026-04-19T00:00:00Z', '[]');
            INSERT INTO forwards (id, forward_id, summary, raw_nodes, source_message_id, source_ctx_id, created_at, updated_at)
            VALUES (1, 'fwd-1', 'sum', '[]', 1001, 1, '2026-04-19T00:00:00Z', '2026-04-19T00:00:00Z');
            INSERT INTO memories (id, content) VALUES (1, 'core-memory');
            """
        )
        conn.commit()


def _make_config(tmp_path: Path) -> Config:
    return Config(
        bot=BotConfig(qq=99999, master=10001, entries=["/y", "/yuu"]),
        daemon=DaemonConfig(recorder_api="http://127.0.0.1:9999"),
        database=DatabaseConfig(path=str(tmp_path / "yuubot.db")),
        recorder=RecorderConfig(media_dir=str(tmp_path / "media")),
        web=WebConfig(
            browser_profile=str(tmp_path / "browser_profile"),
            download_dir=str(tmp_path / "downloads"),
        ),
        response=ResponseConfig(group_default="at", dm_whitelist=[]),
        session=SessionConfig(ttl=300, max_tokens=60000),
        yuuagents={
            "yuutrace": {"db_path": str(tmp_path / "traces.db")},
            "docker": {"image": "yuuagents-runtime:latest"},
        },
    )


def test_parse_categories_supports_plus_and_comma_syntax() -> None:
    assert parse_categories(["core+messages", "traces,messages"]) == (
        "core",
        "messages",
        "traces",
    )


def test_parse_categories_defaults_to_all_when_empty() -> None:
    assert parse_categories([]) == ("core", "messages", "traces")


def test_export_archive_writes_manifest_and_selected_payloads(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    db_path = Path(cfg.database.path).expanduser()
    _seed_database(db_path)

    media_file = Path(cfg.recorder.media_dir).expanduser() / "img.txt"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_text("media", encoding="utf-8")
    Path(cfg.web.browser_profile).mkdir(parents=True, exist_ok=True)
    Path(cfg.web.download_dir).mkdir(parents=True, exist_ok=True)

    traces_db = Path(cfg.yuuagents["yuutrace"]["db_path"]).expanduser()
    traces_db.write_text("trace", encoding="utf-8")

    output = tmp_path / "export.zip"
    manifest = export_archive(cfg, output, ("core", "messages"))

    assert output.is_file()
    assert manifest.categories == ["core", "messages"]
    with zipfile.ZipFile(output, "r") as zf:
        manifest_json = json.loads(zf.read("manifest.json"))
        assert manifest_json["categories"] == ["core", "messages"]
        assert manifest_json["entries"]["core"]["payload_paths"] == [
            "core/db/core.sql",
            "core/browser_profile",
            "core/downloads",
        ]
        assert manifest_json["entries"]["messages"]["payload_paths"] == [
            "messages/db/messages.sql",
            "messages/media",
        ]
        assert "core/db/core.sql" in zf.namelist()
        assert "messages/db/messages.sql" in zf.namelist()
        assert "messages/media/img.txt" in zf.namelist()
        assert "traces/traces.db" not in zf.namelist()
        core_sql = zf.read("core/db/core.sql").decode("utf-8")
        messages_sql = zf.read("messages/db/messages.sql").decode("utf-8")
        assert "INSERT OR REPLACE INTO \"memories\"" in core_sql
        assert "INSERT OR REPLACE INTO \"messages\"" not in core_sql
        assert "memories_fts" not in core_sql
        assert "INSERT OR REPLACE INTO \"messages\"" in messages_sql
        assert "messages_fts" not in messages_sql


def test_import_archive_dry_run_preserves_selected_categories(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    db_path = Path(cfg.database.path).expanduser()
    _seed_database(db_path)

    archive = tmp_path / "core.zip"
    export_archive(cfg, archive, ("core",))

    manifest = import_archive(cfg, archive, categories=("core",), dry_run=True)
    assert manifest.categories == ["core"]
    assert manifest.entries["core"].payload_paths[0] == "core/db/core.sql"


def test_import_archive_restores_selected_payloads_into_destination_paths(tmp_path: Path) -> None:
    source_cfg = _make_config(tmp_path / "source")
    source_db = Path(source_cfg.database.path).expanduser()
    _seed_database(source_db)

    source_media = Path(source_cfg.recorder.media_dir).expanduser() / "nested" / "img.txt"
    source_media.parent.mkdir(parents=True, exist_ok=True)
    source_media.write_text("source-media", encoding="utf-8")

    source_traces = Path(source_cfg.yuuagents["yuutrace"]["db_path"]).expanduser()
    source_traces.parent.mkdir(parents=True, exist_ok=True)
    source_traces.write_text("source-trace", encoding="utf-8")

    archive = tmp_path / "full.zip"
    export_archive(source_cfg, archive, ("core", "messages", "traces"))

    dest_cfg = _make_config(tmp_path / "dest")
    manifest = import_archive(dest_cfg, archive)

    assert manifest.categories == ["core", "messages", "traces"]
    with sqlite3.connect(dest_cfg.database.path) as conn:
        memories = conn.execute("SELECT content FROM memories").fetchall()
        messages = conn.execute("SELECT content FROM messages").fetchall()
    assert memories == [("core-memory",)]
    assert messages == [("hello",)]
    assert (Path(dest_cfg.recorder.media_dir) / "nested" / "img.txt").read_text(encoding="utf-8") == "source-media"
    assert Path(dest_cfg.yuuagents["yuutrace"]["db_path"]).read_text(encoding="utf-8") == "source-trace"


def test_import_archive_rejects_unsupported_manifest_version(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "manifest_version": 999,
                    "created_at": "2026-04-19T00:00:00Z",
                    "source": {"product": "yuubot", "yuubot_version": "x", "yuuagents_version": "y", "deployment_mode": "bare_machine"},
                    "categories": ["core"],
                    "entries": {},
                },
            ),
        )

    cfg = _make_config(tmp_path / "dest")
    try:
        import_archive(cfg, archive, dry_run=True)
    except ValueError as exc:
        assert "unsupported manifest version" in str(exc)
    else:
        raise AssertionError("expected import_archive to reject unsupported manifest version")


def test_export_archive_skips_non_filesystem_trace_uri(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    _seed_database(Path(cfg.database.path))
    cfg.yuuagents["yuutrace"]["db_path"] = f"file:{tmp_path / 'traces.db'}?mode=memory&cache=shared"

    archive = tmp_path / "no-traces.zip"
    manifest = export_archive(cfg, archive, ("core", "traces"))

    assert manifest.categories == ["core", "traces"]
    assert manifest.entries["traces"].payload_paths == []
    with zipfile.ZipFile(archive, "r") as zf:
        assert "traces/traces.db" not in zf.namelist()
