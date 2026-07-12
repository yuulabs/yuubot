from __future__ import annotations

import json
from pathlib import Path

import msgspec

from yuubot.db import Database, migrate, migration_files
from yuubot.domain.models import AliasModelSelector
from yuubot.domain.records import ActorRecord, decode_actor_record
from yuubot.domain.stream import Usage
from yuubot.llm.gateway import AliasRecord, EndpointRecord


async def test_gateway_migration_preserves_actor_selector_and_token_usage(
    tmp_path: Path,
) -> None:
    db = await Database.open(tmp_path / "db", migrate_on_open=False)
    try:
        for version, path in migration_files():
            if version >= 10:
                break
            await db.executescript(path.read_text(encoding="utf-8"))
            await db.execute(
                "insert or replace into app_meta (key, value) values ('schema_version', ?)",
                (str(version),),
            )
            await db.commit()

        old_actor = {
            "id": "amy",
            "name": "Amy",
            "description": "kept",
            "workspace": "/workspace/amy",
            "persona": "kept persona",
            "model": {"selector": "old-model", "vision": True},
            "provider": "old-provider",
            "context_compression_tokens": 1234,
        }
        await db.execute(
            "insert into app_actors (id, payload, enabled, status, updated_at) values (?, ?, 1, 'idle', 'now')",
            ("amy", json.dumps(old_actor).encode()),
        )
        await db.execute(
            "insert into llm_providers (id, name, protocol, config, updated_at) values (?, ?, ?, ?, 'now')",
            ("old-provider", "Old", "openai-compatible", json.dumps({"endpoint": "http://old.test/v1", "api_key": "must-not-survive"}).encode()),
        )
        await db.execute(
            "insert into app_costs (conversation_id, seq, usage, account, estimated, created_at) values ('c1', 0, ?, ?, 1, '2026-01-01T00:00:00+00:00')",
            (json.dumps({"input_tokens": 11, "cached_input_tokens": 2, "output_tokens": 3, "payg_cost": 9.99}), json.dumps({"model": "old-model", "response_cost": 9.99})),
        )
        await db.commit()

        assert await migrate(db) == 14
        tables = await _table_names(db)
        assert "app_gateway_endpoints" in tables
        assert "app_gateway_aliases" in tables
        assert "app_usage" in tables
        assert "app_costs" not in tables
        assert "llm_providers" not in tables

        cursor = await db.execute("select payload from app_gateway_endpoints where id = 'default'")
        endpoint_row = await cursor.fetchone()
        assert endpoint_row is not None
        assert msgspec.json.decode(endpoint_row[0], type=EndpointRecord).base_url == "http://old.test/v1"

        cursor = await db.execute("select payload from app_gateway_aliases where id = 'old-model'")
        alias_row = await cursor.fetchone()
        assert alias_row is not None
        assert msgspec.json.decode(alias_row[0], type=AliasRecord).targets[0].model == "old-model"

        cursor = await db.execute("select payload from app_actors where id = 'amy'")
        actor_row = await cursor.fetchone()
        assert actor_row is not None
        assert decode_actor_record(actor_row[0]) == ActorRecord(
            id="amy",
            name="Amy",
            description="kept",
            workspace="/workspace/amy",
            persona="kept persona",
            model=AliasModelSelector("old-model"),
            context_compression_tokens=1234,
        )

        cursor = await db.execute("select usage, account from app_usage where conversation_id = 'c1'")
        usage_row = await cursor.fetchone()
        assert usage_row is not None
        usage = msgspec.json.decode(usage_row[0], type=Usage)
        assert usage.input_tokens == 11
        assert usage.cached_input_tokens == 2
        assert usage.output_tokens == 3
        assert "cost" not in str(usage_row[0])
        assert "cost" not in str(usage_row[1])
    finally:
        await db.close()


async def _table_names(db: Database) -> set[str]:
    cursor = await db.execute("select name from sqlite_master where type = 'table'")
    return {row[0] for row in await cursor.fetchall()}
