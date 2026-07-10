from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from yuubot.db.database import Database
from yuubot.db.migrate import migrate
from yuubot.integrations import IntegrationRecord


@pytest.mark.asyncio
async def test_migration_renames_tavily_web_integration_to_web(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "db", migrate_on_open=False)
    try:
        await db.executescript(
            """
            create table app_meta (
                key text primary key,
                value text not null
            );
            create table app_integrations (
                type text primary key,
                payload blob not null,
                enabled integer not null,
                last_error blob,
                updated_at text not null
            );
            create table app_routes (
                id text primary key,
                integration_type text not null default '',
                pattern text not null,
                actor_id text not null,
                enabled integer not null default 1,
                created_at text not null,
                updated_at text not null
            );
            create table app_actors (
                id text primary key,
                payload blob not null,
                enabled integer not null,
                status text not null default 'idle',
                last_error blob,
                updated_at text not null
            );
            create table llm_providers (
                id text primary key,
                name text not null,
                protocol text not null,
                config blob not null,
                last_error text,
                updated_at text not null
            );
            create table model_cards (
                provider_id text not null references llm_providers(id) on delete cascade,
                selector text not null,
                payload blob not null,
                updated_at text not null,
                primary key (provider_id, selector)
            );
            """
        )
        old_record = IntegrationRecord(
            "tavily_web",
            "tavily_web",
            "tavily_web",
            {"api_key": "secret", "max_read_chars": 42},
        )
        await db.execute("insert into app_meta (key, value) values ('schema_version', '8')")
        await db.execute(
            "insert into app_integrations (type, payload, enabled, last_error, updated_at) values (?, ?, 1, null, ?)",
            ("tavily_web", msgspec.json.encode(old_record), "2026-01-01T00:00:00Z"),
        )
        await db.execute(
            "insert into app_routes (id, integration_type, pattern, actor_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("route-1", "tavily_web", "/hook", "amy", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        await migrate(db)

        integration_cursor = await db.execute("select type, payload from app_integrations")
        integration_row = await integration_cursor.fetchone()
        assert integration_row is not None
        integration_type, payload = integration_row
        record = msgspec.json.decode(payload, type=IntegrationRecord)
        route_cursor = await db.execute("select integration_type from app_routes where id = 'route-1'")
        route_row = await route_cursor.fetchone()
        assert route_row is not None
        route_type = route_row[0]

        assert integration_type == "web"
        assert record.id == "web"
        assert record.type == "web"
        assert record.name == "web"
        assert record.config == {"max_read_chars": 42, "tavily_api_key": "secret"}
        assert route_type == "web"
    finally:
        await db.close()
