"""Durable application state persisted in SQLite."""

from pathlib import Path

import msgspec
from attrs import define

from ..db import Database
from ..util.time import utc_now_iso
from ..db.migrate import current_version
from ..domain.messages import ModelCard
from ..domain.records import (
    ActorRecord,
    ActorStatus,
    ConversationRow,
    CostRow,
    IntegrationStatus,
    LifecycleError,
    RouteRecord,
    decode_actor_record,
    decode_lifecycle_error,
)
from ..domain.stream import Usage
from ..integrations import IntegrationRecord
from .mcp import McpCapabilityIndex, McpServerRecord
from .skills import SkillRecord
from .auth_attempts import AuthAttempt
from ..llm.records import ProviderRecord
from .shares import ShareGrant


@define
class ApplicationStateStore:
    _db: Database

    @property
    def path(self) -> Path:
        return self._db.path

    async def schema_version(self) -> int:
        return await current_version(self._db)

    async def list_providers(self) -> list[ProviderRecord]:
        cursor = await self._db.execute(
            "select id, name, protocol, config, last_error from llm_providers order by id"
        )
        rows = await cursor.fetchall()
        return [
            ProviderRecord(
                id=provider_id,
                name=name,
                protocol=protocol,
                config=msgspec.json.decode(config, type=dict[str, object]),
                last_error=last_error,
            )
            for provider_id, name, protocol, config, last_error in rows
        ]

    async def load_provider(self, provider_id: str) -> ProviderRecord:
        cursor = await self._db.execute(
            "select id, name, protocol, config, last_error from llm_providers where id = ?",
            (provider_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(provider_id)
        provider_id_value, name, protocol, config, last_error = row
        return ProviderRecord(
            id=provider_id_value,
            name=name,
            protocol=protocol,
            config=msgspec.json.decode(config, type=dict[str, object]),
            last_error=last_error,
        )

    async def put_provider(self, record: ProviderRecord) -> None:
        await self._db.execute(
            """
            insert into llm_providers (id, name, protocol, config, last_error, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                name = excluded.name,
                protocol = excluded.protocol,
                config = excluded.config,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                record.name,
                record.protocol,
                msgspec.json.encode(record.config),
                record.last_error,
                utc_now_iso(),
            ),
        )
        await self._db.commit()

    async def set_provider_last_error(self, provider_id: str, last_error: str | None) -> None:
        await self._db.execute(
            "update llm_providers set last_error = ?, updated_at = ? where id = ?",
            (last_error, utc_now_iso(), provider_id),
        )
        await self._db.commit()

    async def delete_provider(self, provider_id: str) -> bool:
        cursor = await self._db.execute("delete from llm_providers where id = ?", (provider_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_model_cards(self, provider_id: str) -> list[ModelCard]:
        cursor = await self._db.execute(
            "select payload from model_cards where provider_id = ? order by selector",
            (provider_id,),
        )
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=ModelCard) for payload, in rows]

    async def load_model_card(self, provider_id: str, selector: str) -> ModelCard | None:
        cursor = await self._db.execute(
            "select payload from model_cards where provider_id = ? and selector = ?",
            (provider_id, selector),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return msgspec.json.decode(row[0], type=ModelCard)

    async def upsert_model_card(self, provider_id: str, card: ModelCard) -> None:
        await self._db.execute(
            """
            insert into model_cards (provider_id, selector, payload, updated_at)
            values (?, ?, ?, ?)
            on conflict(provider_id, selector) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (provider_id, card.selector, msgspec.json.encode(card), utc_now_iso()),
        )
        await self._db.commit()

    async def delete_model_card(self, provider_id: str, selector: str) -> bool:
        cursor = await self._db.execute(
            "delete from model_cards where provider_id = ? and selector = ?",
            (provider_id, selector),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def load_integrations(self) -> list[tuple[IntegrationRecord, bool, dict[str, object] | None]]:
        cursor = await self._db.execute("select payload, enabled, last_error from app_integrations order by type")
        rows = await cursor.fetchall()
        return [
            (
                msgspec.json.decode(payload, type=IntegrationRecord),
                bool(enabled),
                msgspec.json.decode(last_error, type=dict[str, object]) if last_error is not None else None,
            )
            for payload, enabled, last_error in rows
        ]

    async def put_integration(self, record: IntegrationRecord, *, enabled: bool, last_error: LifecycleError | None = None) -> None:
        await self._db.execute(
            "insert or replace into app_integrations (type, payload, enabled, last_error, updated_at) values (?, ?, ?, ?, ?)",
            (record.type, msgspec.json.encode(record), int(enabled), _error_payload(last_error), utc_now_iso()),
        )
        await self._db.commit()

    async def set_integration_enabled(self, integration_type: str, *, enabled: bool, last_error: LifecycleError | None = None) -> None:
        await self._db.execute(
            "update app_integrations set enabled = ?, last_error = ?, updated_at = ? where type = ?",
            (int(enabled), _error_payload(last_error), utc_now_iso(), integration_type),
        )
        await self._db.commit()

    async def integration_statuses(self) -> dict[str, IntegrationStatus]:
        cursor = await self._db.execute("select type, enabled, last_error from app_integrations")
        rows = await cursor.fetchall()
        return {
            integration_type: IntegrationStatus(
                enabled=bool(enabled),
                last_error=decode_lifecycle_error(last_error),
            )
            for integration_type, enabled, last_error in rows
        }

    async def load_mcp_servers(self) -> list[tuple[McpServerRecord, bool, str | None, McpCapabilityIndex | None]]:
        cursor = await self._db.execute(
            "select payload, enabled, last_error, capabilities from app_mcp_servers order by id"
        )
        rows = await cursor.fetchall()
        return [
            (
                msgspec.json.decode(payload, type=McpServerRecord),
                bool(enabled),
                last_error,
                msgspec.json.decode(capabilities, type=McpCapabilityIndex) if capabilities is not None else None,
            )
            for payload, enabled, last_error, capabilities in rows
        ]

    async def put_mcp_server(
        self,
        record: McpServerRecord,
        *,
        enabled: bool,
        last_error: str | None = None,
        capabilities: McpCapabilityIndex | None = None,
    ) -> None:
        await self._db.execute(
            """
            insert into app_mcp_servers (id, payload, enabled, last_error, capabilities, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                enabled = excluded.enabled,
                last_error = excluded.last_error,
                capabilities = coalesce(excluded.capabilities, app_mcp_servers.capabilities),
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                msgspec.json.encode(record),
                int(enabled),
                last_error,
                msgspec.json.encode(capabilities) if capabilities is not None else None,
                utc_now_iso(),
            ),
        )
        await self._db.commit()

    async def set_mcp_server_enabled(self, server_id: str, *, enabled: bool, last_error: str | None = None) -> None:
        await self._db.execute(
            "update app_mcp_servers set enabled = ?, last_error = ?, updated_at = ? where id = ?",
            (int(enabled), last_error, utc_now_iso(), server_id),
        )
        await self._db.commit()

    async def delete_mcp_server(self, server_id: str) -> bool:
        cursor = await self._db.execute("delete from app_mcp_servers where id = ?", (server_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def load_skills(self) -> list[SkillRecord]:
        cursor = await self._db.execute("select payload from app_skills order by id")
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=SkillRecord) for payload, in rows]

    async def put_skill(self, record: SkillRecord) -> None:
        await self._db.execute(
            """
            insert into app_skills (id, payload, updated_at)
            values (?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (record.id, msgspec.json.encode(record), utc_now_iso()),
        )
        await self._db.commit()

    async def delete_skill(self, skill_id: str) -> bool:
        cursor = await self._db.execute("delete from app_skills where id = ?", (skill_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def load_auth_attempts(self) -> list[AuthAttempt]:
        cursor = await self._db.execute("select payload from app_auth_attempts order by updated_at desc")
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=AuthAttempt) for payload, in rows]

    async def put_auth_attempt(self, attempt: AuthAttempt) -> None:
        await self._db.execute(
            """
            insert into app_auth_attempts (id, payload, updated_at)
            values (?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (attempt.id, msgspec.json.encode(attempt), utc_now_iso()),
        )
        await self._db.commit()

    async def delete_auth_attempt(self, attempt_id: str) -> bool:
        cursor = await self._db.execute("delete from app_auth_attempts where id = ?", (attempt_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def load_actor_records(self) -> list[tuple[ActorRecord, bool]]:
        cursor = await self._db.execute("select payload, enabled from app_actors order by id")
        rows = await cursor.fetchall()
        return [(decode_actor_record(payload), bool(enabled)) for payload, enabled in rows]

    async def put_actor(self, record: ActorRecord, *, enabled: bool = True, status: str = "idle", last_error: LifecycleError | None = None) -> None:
        await self._db.execute(
            "insert or replace into app_actors (id, payload, enabled, status, last_error, updated_at) values (?, ?, ?, ?, ?, ?)",
            (record.id, msgspec.json.encode(record), int(enabled), status, _error_payload(last_error), utc_now_iso()),
        )
        await self._db.commit()

    async def delete_actor(self, actor_id: str) -> None:
        await self._db.execute("delete from app_actors where id = ?", (actor_id,))
        await self._db.commit()

    async def set_actor_status(self, actor_id: str, status: str, last_error: LifecycleError | None = None, *, enabled: bool | None = None) -> None:
        if enabled is None:
            await self._db.execute(
                "update app_actors set status = ?, last_error = ?, updated_at = ? where id = ?",
                (status, _error_payload(last_error), utc_now_iso(), actor_id),
            )
        else:
            await self._db.execute(
                "update app_actors set enabled = ?, status = ?, last_error = ?, updated_at = ? where id = ?",
                (int(enabled), status, _error_payload(last_error), utc_now_iso(), actor_id),
            )
        await self._db.commit()

    async def actor_statuses(self) -> dict[str, ActorStatus]:
        cursor = await self._db.execute("select id, enabled, status, last_error from app_actors")
        rows = await cursor.fetchall()
        return {
            actor_id: ActorStatus(
                enabled=bool(enabled),
                status=status,
                last_error=decode_lifecycle_error(last_error),
            )
            for actor_id, enabled, status, last_error in rows
        }

    async def put_conversation(self, conversation_id: str, actor_id: str, status: str, last_error: dict[str, object] | None = None) -> None:
        timestamp = utc_now_iso()
        error_payload = msgspec.json.encode(last_error) if last_error is not None else None
        await self._db.execute(
            """
            insert into app_conversations (id, actor_id, status, created_at, last_active_at, last_error, title)
            values (?, ?, ?, ?, ?, ?, '')
            on conflict(id) do update set
                actor_id = excluded.actor_id,
                status = excluded.status,
                last_active_at = excluded.last_active_at,
                last_error = excluded.last_error
            """,
            (conversation_id, actor_id, status, timestamp, timestamp, error_payload),
        )
        await self._db.commit()

    async def set_conversation_title_if_empty(self, conversation_id: str, title: str) -> None:
        if not title:
            return
        await self._db.execute(
            "update app_conversations set title = ? where id = ? and title = ''",
            (title, conversation_id),
        )
        await self._db.commit()

    async def list_conversations(self) -> list[ConversationRow]:
        cursor = await self._db.execute(
            """
            select id, actor_id, status, created_at, last_active_at, last_error, title
            from app_conversations
            order by last_active_at desc
            """
        )
        rows = await cursor.fetchall()
        return [
            ConversationRow(
                id=conversation_id,
                actor_id=actor_id,
                status=status,
                created_at=created_at,
                last_active_at=last_active_at,
                last_error=msgspec.json.decode(last_error, type=dict[str, object]) if last_error is not None else None,
                title=title,
            )
            for conversation_id, actor_id, status, created_at, last_active_at, last_error, title in rows
        ]

    async def delete_conversation(self, conversation_id: str) -> bool:
        conversation = await self._db.execute("delete from app_conversations where id = ?", (conversation_id,))
        costs = await self._db.execute("delete from app_costs where conversation_id = ?", (conversation_id,))
        await self._db.commit()
        return conversation.rowcount > 0 or costs.rowcount > 0

    async def append_cost(self, conversation_id: str, usage: Usage, account: dict[str, object], *, estimated: bool) -> CostRow:
        created_at = utc_now_iso()
        usage_payload = msgspec.json.encode(usage)
        account_payload = msgspec.json.encode(account)
        async with self._db.transaction():
            cursor = await self._db.execute(
                "select coalesce(max(seq) + 1, 0) from app_costs where conversation_id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("cost sequence query returned no row")
            seq = row[0]
            await self._db.execute(
                "insert into app_costs (conversation_id, seq, usage, account, estimated, created_at) values (?, ?, ?, ?, ?, ?)",
                (conversation_id, seq, usage_payload, account_payload, int(estimated), created_at),
            )
        return CostRow(
            conversation_id=conversation_id,
            seq=seq,
            usage=usage,
            account=dict(account),
            estimated=estimated,
            created_at=created_at,
        )

    async def load_routes(self) -> list[RouteRecord]:
        cursor = await self._db.execute(
            "select id, integration_type, pattern, actor_id, enabled from app_routes order by id"
        )
        rows = await cursor.fetchall()
        return [
            RouteRecord(
                id=route_id,
                integration_type=integration_type,
                pattern=pattern,
                actor_id=actor_id,
                enabled=bool(enabled),
            )
            for route_id, integration_type, pattern, actor_id, enabled in rows
        ]

    async def put_route(self, record: RouteRecord) -> None:
        timestamp = utc_now_iso()
        await self._db.execute(
            """
            insert into app_routes (id, integration_type, pattern, actor_id, enabled, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                integration_type = excluded.integration_type,
                pattern = excluded.pattern,
                actor_id = excluded.actor_id,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                record.integration_type,
                record.pattern,
                record.actor_id,
                int(record.enabled),
                timestamp,
                timestamp,
            ),
        )
        await self._db.commit()

    async def delete_route(self, route_id: str) -> bool:
        cursor = await self._db.execute("delete from app_routes where id = ?", (route_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def load_share_grants(self) -> list[ShareGrant]:
        cursor = await self._db.execute("select payload from app_share_grants order by id")
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=ShareGrant) for payload, in rows]

    async def put_share_grant(self, grant: ShareGrant) -> None:
        timestamp = utc_now_iso()
        await self._db.execute(
            """
            insert into app_share_grants (id, payload, created_at, updated_at)
            values (?, ?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (grant.id, msgspec.json.encode(grant), grant.created_at, timestamp),
        )
        await self._db.commit()

    async def delete_share_grant(self, share_id: str) -> bool:
        cursor = await self._db.execute("delete from app_share_grants where id = ?", (share_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def load_costs(self, conversation_id: str) -> list[CostRow]:
        cursor = await self._db.execute(
            "select seq, usage, account, estimated, created_at from app_costs where conversation_id = ? order by seq",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [
            CostRow(
                conversation_id=conversation_id,
                seq=seq,
                usage=msgspec.json.decode(usage, type=Usage),
                account=msgspec.json.decode(account, type=dict[str, object]) if account is not None else {},
                estimated=bool(estimated),
                created_at=created_at,
            )
            for seq, usage, account, estimated, created_at in rows
        ]

def _error_payload(error: LifecycleError | dict[str, object] | None) -> bytes | None:
    if error is None:
        return None
    return msgspec.json.encode(error)
