"""Durable application state persisted in SQLite."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar, cast

import msgspec
from attrs import define

from ..db import Database
from ..util.time import utc_now_iso
from ..db.migrate import current_version

from ..domain.records import (
    ActorRecord,
    ActorStatus,
    ConversationRow,
    UsageRow,
    IntegrationStatus,
    LifecycleError,
    RouteRecord,
    decode_actor_record,
    decode_lifecycle_error,
)
from ..domain.stream import Usage
from ..integrations import IntegrationRecord
from ..llm.gateway import AliasRecord, EndpointRecord, validate_alias, validate_endpoint
from .mcp import McpCapabilityIndex, McpServerRecord
from .skills import SkillRecord
from .auth_attempts import AuthAttempt

from .shares import ShareGrant


T = TypeVar("T")


@define(frozen=True)
class Table(Generic[T]):
    """Descriptor for a store table backed by a generic CRUD implementation.

    `to_row` produces the ordered column values for an insert/update (the
    `extra` mapping carries scalar columns derived from call-site arguments
    rather than the record itself). `from_row` reconstructs a value from a
    fetched row in the same column order.
    """

    name: str
    id_columns: tuple[str, ...]
    record_type: type[T]
    columns: tuple[str, ...]
    to_row: Callable[[T, Mapping[str, Any]], Sequence[Any]]
    from_row: Callable[[Sequence[Any]], T]
    order_by: str | None = None
    replace: bool = False
    conflict_update: str | None = None
    updated_at: bool = True


@define
class ApplicationStateStore:
    _db: Database

    @property
    def path(self) -> Path:
        return self._db.path

    async def schema_version(self) -> int:
        return await current_version(self._db)

    async def list_gateway_endpoints(self) -> list[EndpointRecord]:
        cursor = await self._db.execute(
            "select payload from app_gateway_endpoints order by id"
        )
        rows = list(await cursor.fetchall())
        endpoints = [msgspec.json.decode(row[0], type=EndpointRecord) for row in rows]
        for endpoint in endpoints:
            validate_endpoint(endpoint)
        return endpoints

    async def put_gateway_endpoint(self, endpoint: EndpointRecord) -> None:
        validate_endpoint(endpoint)
        await self._db.execute(
            """
            insert into app_gateway_endpoints (id, payload, updated_at)
            values (?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (endpoint.id, msgspec.json.encode(endpoint), utc_now_iso()),
        )
        await self._db.commit()

    async def delete_gateway_endpoint(self, endpoint_id: str) -> bool:
        cursor = await self._db.execute(
            "delete from app_gateway_endpoints where id = ?", (endpoint_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_gateway_aliases(self) -> list[AliasRecord]:
        cursor = await self._db.execute(
            "select payload from app_gateway_aliases order by id"
        )
        rows = await cursor.fetchall()
        aliases = [msgspec.json.decode(row[0], type=AliasRecord) for row in rows]
        for alias in aliases:
            validate_alias(alias)
        return aliases

    async def put_gateway_alias(self, alias: AliasRecord) -> None:
        validate_alias(alias)
        await self._db.execute(
            """
            insert into app_gateway_aliases (id, payload, updated_at)
            values (?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (alias.id, msgspec.json.encode(alias), utc_now_iso()),
        )
        await self._db.commit()

    async def delete_gateway_alias(self, alias_id: str) -> bool:
        cursor = await self._db.execute(
            "delete from app_gateway_aliases where id = ?", (alias_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # --- generic CRUD primitives -------------------------------------------------

    async def _store_list(self, table: Table[T]) -> list[T]:
        columns = ", ".join(table.columns)
        sql = f"select {columns} from {table.name}"
        if table.order_by:
            sql += f" order by {table.order_by}"
        cursor = await self._db.execute(sql)
        rows = await cursor.fetchall()
        return [table.from_row(tuple(row)) for row in rows]

    async def _store_load(self, table: Table[T], *id_values: object) -> T:
        if len(id_values) != len(table.id_columns):
            raise TypeError("id value count must match id_columns")
        columns = ", ".join(table.columns)
        where = " and ".join(f"{col} = ?" for col in table.id_columns)
        cursor = await self._db.execute(
            f"select {columns} from {table.name} where {where}",
            tuple(id_values),
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(id_values)
        return table.from_row(tuple(row))

    async def _store_delete(self, table: Table[Any], *id_values: object) -> bool:
        if len(id_values) != len(table.id_columns):
            raise TypeError("id value count must match id_columns")
        where = " and ".join(f"{col} = ?" for col in table.id_columns)
        cursor = await self._db.execute(
            f"delete from {table.name} where {where}",
            tuple(id_values),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def _store_put(self, table: Table[T], record: T, **extra: Any) -> None:
        columns = list(table.columns)
        if table.updated_at:
            columns.append("updated_at")
        placeholders = ", ".join("?" for _ in columns)
        values: list[Any] = list(table.to_row(record, extra))
        if table.updated_at:
            values.append(utc_now_iso())
        value_tuple = tuple(values)
        if table.replace:
            sql = f"insert or replace into {table.name} ({', '.join(columns)}) values ({placeholders})"
        elif table.conflict_update is not None:
            sql = (
                f"insert into {table.name} ({', '.join(columns)}) values ({placeholders}) "
                f"on conflict({', '.join(table.id_columns)}) do update set {table.conflict_update}"
            )
        else:
            set_clause = ", ".join(f"{col} = excluded.{col}" for col in table.columns)
            sql = (
                f"insert into {table.name} ({', '.join(columns)}) values ({placeholders}) "
                f"on conflict({', '.join(table.id_columns)}) do update set {set_clause}"
            )
        await self._db.execute(sql, value_tuple)
        await self._db.commit()

    async def _store_set_col(self, table: Table[Any], column: str, value: Any, *id_values: object) -> None:
        if len(id_values) != len(table.id_columns):
            raise TypeError("id value count must match id_columns")
        where = " and ".join(f"{col} = ?" for col in table.id_columns)
        await self._db.execute(
            f"update {table.name} set {column} = ?, updated_at = ? where {where}",
            (value, utc_now_iso(), *id_values),
        )
        await self._db.commit()

    # --- integrations ------------------------------------------------------------

    _INTEGRATIONS: Table[Any] = Table(
        name="app_integrations",
        id_columns=("type",),
        record_type=IntegrationRecord,
        columns=("type", "payload", "enabled", "last_error"),
        order_by="type",
        replace=True,
        to_row=lambda r, x: (r.type, msgspec.json.encode(r), int(x["enabled"]), _error_payload(x["last_error"])),
        from_row=lambda row: (
            msgspec.json.decode(row[1], type=IntegrationRecord),
            bool(row[2]),
            msgspec.json.decode(row[3], type=dict[str, object]) if row[3] is not None else None,
        ),
    )

    async def load_integrations(self) -> list[tuple[IntegrationRecord, bool, dict[str, object] | None]]:
        return cast(list[tuple[IntegrationRecord, bool, dict[str, object] | None]], await self._store_list(self._INTEGRATIONS))

    async def put_integration(self, record: IntegrationRecord, enabled: bool, last_error: LifecycleError | None = None) -> None:
        await self._store_put(self._INTEGRATIONS, record, enabled=enabled, last_error=last_error)

    async def set_integration_enabled(self, integration_type: str, enabled: bool, last_error: LifecycleError | None = None) -> None:
        await self._store_set_col(self._INTEGRATIONS, "enabled", int(enabled), integration_type)
        await self._store_set_col(self._INTEGRATIONS, "last_error", _error_payload(last_error), integration_type)

    async def integration_statuses(self) -> dict[str, IntegrationStatus]:
        cursor = await self._db.execute("select type, enabled, last_error from app_integrations")
        rows = await cursor.fetchall()
        return {
            integration_type: IntegrationStatus(
                bool(enabled),
                decode_lifecycle_error(last_error),
            )
            for integration_type, enabled, last_error in rows
        }

    # --- mcp servers -------------------------------------------------------------

    _MCP: Table[Any] = Table(
        name="app_mcp_servers",
        id_columns=("id",),
        record_type=McpServerRecord,
        columns=("id", "payload", "enabled", "last_error", "capabilities"),
        order_by="id",
        conflict_update=(
            "payload = excluded.payload, enabled = excluded.enabled, last_error = excluded.last_error, "
            "capabilities = coalesce(excluded.capabilities, app_mcp_servers.capabilities)"
        ),
        to_row=lambda r, x: (
            r.id,
            msgspec.json.encode(r),
            int(x["enabled"]),
            x["last_error"],
            msgspec.json.encode(x["capabilities"]) if x["capabilities"] is not None else None,
        ),
        from_row=lambda row: (
            msgspec.json.decode(row[1], type=McpServerRecord),
            bool(row[2]),
            row[3],
            msgspec.json.decode(row[4], type=McpCapabilityIndex) if row[4] is not None else None,
        ),
    )

    async def load_mcp_servers(self) -> list[tuple[McpServerRecord, bool, str | None, McpCapabilityIndex | None]]:
        return cast(
            list[tuple[McpServerRecord, bool, str | None, McpCapabilityIndex | None]],
            await self._store_list(self._MCP),
        )

    async def put_mcp_server(
        self,
        record: McpServerRecord,
        enabled: bool,
        last_error: str | None = None,
        capabilities: McpCapabilityIndex | None = None,
    ) -> None:
        await self._store_put(self._MCP, record, enabled=enabled, last_error=last_error, capabilities=capabilities)

    async def set_mcp_server_enabled(self, server_id: str, enabled: bool, last_error: str | None = None) -> None:
        await self._store_set_col(self._MCP, "enabled", int(enabled), server_id)
        await self._store_set_col(self._MCP, "last_error", last_error, server_id)

    async def delete_mcp_server(self, server_id: str) -> bool:
        return await self._store_delete(self._MCP, server_id)

    # --- skills ------------------------------------------------------------------

    _SKILLS = Table[SkillRecord](
        name="app_skills",
        id_columns=("id",),
        record_type=SkillRecord,
        columns=("id", "payload"),
        order_by="id",
        to_row=lambda r, _: (r.id, msgspec.json.encode(r)),
        from_row=lambda row: msgspec.json.decode(row[1], type=SkillRecord),
    )

    async def load_skills(self) -> list[SkillRecord]:
        return await self._store_list(self._SKILLS)

    async def put_skill(self, record: SkillRecord) -> None:
        await self._store_put(self._SKILLS, record)

    async def delete_skill(self, skill_id: str, tombstone: bool = False) -> bool:
        async with self._db.transaction():
            cursor = await self._db.execute("delete from app_skills where id = ?", (skill_id,))
            if tombstone:
                await self._db.execute("insert or replace into app_skill_tombstones (id) values (?)", (skill_id,))
            else:
                await self._db.execute("delete from app_skill_tombstones where id = ?", (skill_id,))
        return cursor.rowcount > 0

    async def load_skill_tombstones(self) -> set[str]:
        cursor = await self._db.execute("select id from app_skill_tombstones")
        return {str(row[0]) for row in await cursor.fetchall()}

    async def clear_skill_tombstone(self, skill_id: str) -> None:
        await self._db.execute("delete from app_skill_tombstones where id = ?", (skill_id,))
        await self._db.commit()

    # --- auth attempts -----------------------------------------------------------

    _AUTH_ATTEMPTS = Table[AuthAttempt](
        name="app_auth_attempts",
        id_columns=("id",),
        record_type=AuthAttempt,
        columns=("id", "payload"),
        order_by="updated_at desc",
        to_row=lambda r, _: (r.id, msgspec.json.encode(r)),
        from_row=lambda row: msgspec.json.decode(row[1], type=AuthAttempt),
    )

    async def load_auth_attempts(self) -> list[AuthAttempt]:
        return await self._store_list(self._AUTH_ATTEMPTS)

    async def put_auth_attempt(self, attempt: AuthAttempt) -> None:
        await self._store_put(self._AUTH_ATTEMPTS, attempt)

    async def delete_auth_attempt(self, attempt_id: str) -> bool:
        return await self._store_delete(self._AUTH_ATTEMPTS, attempt_id)

    # --- actors ------------------------------------------------------------------

    _ACTORS: Table[Any] = Table(
        name="app_actors",
        id_columns=("id",),
        record_type=ActorRecord,
        columns=("id", "payload", "enabled"),
        order_by="id",
        replace=True,
        to_row=lambda r, x: (r.id, msgspec.json.encode(r), int(x["enabled"])),
        from_row=lambda row: (decode_actor_record(row[1]), bool(row[2])),
    )

    async def load_actor_records(self) -> list[tuple[ActorRecord, bool]]:
        return cast(list[tuple[ActorRecord, bool]], await self._store_list(self._ACTORS))

    async def put_actor(self, record: ActorRecord, enabled: bool = True, status: str = "idle", last_error: LifecycleError | None = None) -> None:
        await self._store_put(self._ACTORS, record, enabled=enabled)
        await self._store_set_col(self._ACTORS, "status", status, record.id)
        await self._store_set_col(self._ACTORS, "last_error", _error_payload(last_error), record.id)

    async def delete_actor(self, actor_id: str) -> None:
        await self._db.execute("delete from app_actors where id = ?", (actor_id,))
        await self._db.commit()

    async def set_actor_status(self, actor_id: str, status: str, last_error: LifecycleError | None = None, enabled: bool | None = None) -> None:
        if enabled is None:
            await self._store_set_col(self._ACTORS, "status", status, actor_id)
            await self._store_set_col(self._ACTORS, "last_error", _error_payload(last_error), actor_id)
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
                bool(enabled),
                status,
                decode_lifecycle_error(last_error),
            )
            for actor_id, enabled, status, last_error in rows
        }

    # --- conversations -----------------------------------------------------------

    _CONVERSATIONS = Table[ConversationRow](
        name="app_conversations",
        id_columns=("id",),
        record_type=ConversationRow,
        columns=("id", "actor_id", "status", "created_at", "last_active_at", "last_error", "title"),
        order_by="last_active_at desc",
        to_row=lambda r, _: (
            r.id,
            r.actor_id,
            r.status,
            r.created_at,
            r.last_active_at,
            msgspec.json.encode(r.last_error) if r.last_error is not None else None,
            r.title,
        ),
        from_row=lambda row: ConversationRow(
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            msgspec.json.decode(row[5], type=dict[str, object]) if row[5] is not None else None,
            row[6],
        ),
    )

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

    async def get_conversation(self, conversation_id: str) -> ConversationRow | None:
        try:
            return await self._store_load(self._CONVERSATIONS, conversation_id)
        except KeyError:
            return None

    async def list_conversations(self) -> list[ConversationRow]:
        return await self._store_list(self._CONVERSATIONS)

    async def delete_conversation(self, conversation_id: str) -> bool:
        conversation = await self._db.execute("delete from app_conversations where id = ?", (conversation_id,))
        usage = await self._db.execute("delete from app_usage where conversation_id = ?", (conversation_id,))
        await self._db.commit()
        return conversation.rowcount > 0 or usage.rowcount > 0

    async def get_response_state(self, conversation_id: str) -> dict[str, object]:
        cursor = await self._db.execute(
            "select payload from app_conversation_response_state where conversation_id = ?",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {}
        return dict(msgspec.json.decode(row[0], type=dict[str, object]))

    async def put_response_state(
        self, conversation_id: str, state: dict[str, object]
    ) -> None:
        await self._db.execute(
            """
            insert into app_conversation_response_state (conversation_id, payload, updated_at)
            values (?, ?, ?)
            on conflict(conversation_id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (conversation_id, msgspec.json.encode(state), utc_now_iso()),
        )
        await self._db.commit()

    # --- usage -------------------------------------------------------------------

    async def append_usage(self, conversation_id: str, usage: Usage, account: dict[str, object]) -> UsageRow:
        created_at = utc_now_iso()
        usage_payload = msgspec.json.encode(usage)
        account_payload = msgspec.json.encode(account)
        async with self._db.transaction():
            cursor = await self._db.execute(
                "select coalesce(max(seq) + 1, 0) from app_usage where conversation_id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("usage sequence query returned no row")
            seq = row[0]
            await self._db.execute(
                "insert into app_usage (conversation_id, seq, usage, account, created_at) values (?, ?, ?, ?, ?)",
                (conversation_id, seq, usage_payload, account_payload, created_at),
            )
        return UsageRow(
            conversation_id,
            seq,
            usage,
            dict(account),
            created_at,
        )

    async def load_usage(self, conversation_id: str) -> list[UsageRow]:
        cursor = await self._db.execute(
            "select seq, usage, account, created_at from app_usage where conversation_id = ? order by seq",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [
            UsageRow(
                conversation_id,
                seq,
                msgspec.json.decode(usage, type=Usage),
                msgspec.json.decode(account, type=dict[str, object]) if account is not None else {},
                created_at,
            )
            for seq, usage, account, created_at in rows
        ]

    async def usage_dashboard(self, since: str | None = None) -> dict[str, object]:
        query = "select usage, account from app_usage"
        params: tuple[object, ...] = ()
        if since is not None:
            query += " where created_at >= ?"
            params = (since,)
        cursor = await self._db.execute(query, params)
        rows = list(await cursor.fetchall())
        input_tokens = cached_input_tokens = cache_write_tokens = output_tokens = 0
        latency_total = 0.0
        latency_samples = 0
        fallback_requests = 0
        endpoints: dict[str, int] = {}
        models: dict[str, int] = {}
        for usage_payload, account_payload in rows:
            usage = msgspec.json.decode(usage_payload, type=Usage)
            account = (
                msgspec.json.decode(account_payload, type=dict[str, object])
                if account_payload is not None
                else {}
            )
            input_tokens += usage.input_tokens
            cached_input_tokens += usage.cached_input_tokens
            cache_write_tokens += usage.cache_write_tokens
            output_tokens += usage.output_tokens
            latency = account.get("gateway_latency_ms")
            if isinstance(latency, int | float) and not isinstance(latency, bool):
                latency_total += float(latency)
                latency_samples += 1
            endpoint_id = account.get("endpoint_id")
            model = account.get("model")
            if isinstance(endpoint_id, str) and endpoint_id:
                endpoints[endpoint_id] = endpoints.get(endpoint_id, 0) + 1
            if isinstance(model, str) and model:
                key = f"{endpoint_id}/{model}" if isinstance(endpoint_id, str) else model
                models[key] = models.get(key, 0) + 1
            path = account.get("fallback_path")
            if isinstance(path, list) and len(path) > 1:
                fallback_requests += 1
        return {
            "requests": len(rows),
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_tokens": cache_write_tokens,
            "output_tokens": output_tokens,
            "avg_gateway_latency_ms": latency_total / latency_samples if latency_samples else 0,
            "fallback_requests": fallback_requests,
            "endpoints": [
                {"name": name, "requests": count}
                for name, count in sorted(endpoints.items(), key=lambda item: (-item[1], item[0]))
            ],
            "models": [
                {"name": name, "requests": count}
                for name, count in sorted(models.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    # --- routes ------------------------------------------------------------------

    _ROUTES = Table[RouteRecord](
        name="app_routes",
        id_columns=("id",),
        record_type=RouteRecord,
        columns=("id", "integration_type", "pattern", "actor_id", "enabled", "created_at"),
        order_by="id",
        conflict_update=(
            "integration_type = excluded.integration_type, pattern = excluded.pattern, "
            "actor_id = excluded.actor_id, enabled = excluded.enabled"
        ),
        to_row=lambda r, _: (r.id, r.integration_type, r.pattern, r.actor_id, int(r.enabled), utc_now_iso()),
        from_row=lambda row: RouteRecord(
            id=row[0],
            integration_type=row[1],
            pattern=row[2],
            actor_id=row[3],
            enabled=bool(row[4]),
        ),
    )

    async def load_routes(self) -> list[RouteRecord]:
        return await self._store_list(self._ROUTES)

    async def put_route(self, record: RouteRecord) -> None:
        await self._store_put(self._ROUTES, record)

    async def delete_route(self, route_id: str) -> bool:
        return await self._store_delete(self._ROUTES, route_id)

    # --- share grants ------------------------------------------------------------

    _SHARE_GRANTS = Table[ShareGrant](
        name="app_share_grants",
        id_columns=("id",),
        record_type=ShareGrant,
        columns=("id", "payload", "created_at"),
        order_by="id",
        to_row=lambda r, _: (r.id, msgspec.json.encode(r), r.created_at),
        from_row=lambda row: msgspec.json.decode(row[1], type=ShareGrant),
    )

    async def load_share_grants(self) -> list[ShareGrant]:
        return await self._store_list(self._SHARE_GRANTS)

    async def put_share_grant(self, grant: ShareGrant) -> None:
        await self._store_put(self._SHARE_GRANTS, grant)

    async def delete_share_grant(self, share_id: str) -> bool:
        return await self._store_delete(self._SHARE_GRANTS, share_id)


def _error_payload(error: LifecycleError | dict[str, object] | None) -> bytes | None:
    if error is None:
        return None
    return msgspec.json.encode(error)
