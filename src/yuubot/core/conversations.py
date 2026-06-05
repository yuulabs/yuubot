"""Conversation-mode storage and agent lifecycle coordination."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

import msgspec
import yuullm
from tortoise import connections
from yuuagents.eventbus import RuntimeEvent

from yuubot.core.actors.manager import ActorManager
from yuubot.resources.records import (
    ConversationMessageRecord,
    ConversationRecord,
)
from yuubot.resources.store.resource import Store


@dataclass(frozen=True)
class AgentEvent:
    conversation_id: str
    agent_id: str
    agent_name: str
    event_type: str
    content: dict[str, object]
    timestamp: float

    def as_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "event_type": self.event_type,
            "content": self.content,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class AgentEventIdentity:
    """Typed extraction of identity fields from RuntimeEvent.data."""

    agent_id: str
    entity_id: str = ""
    parent_id: str = ""

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> AgentEventIdentity:
        data = event.data
        return cls(
            agent_id=event.agent_id or "",
            entity_id=str(data.get("entity_id") or ""),
            parent_id=str(data.get("parent_id") or ""),
        )


@dataclass(frozen=True)
class EntityData:
    """Typed extraction of entity fields from RuntimeEvent.data."""

    entity_id: str = ""
    entity_type: str = ""
    parent_id: str = ""
    tool_call_id: str = ""
    status: str = ""

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> EntityData:
        data = event.data
        return cls(
            entity_id=str(data.get("entity_id") or ""),
            entity_type=str(data.get("entity_type") or ""),
            parent_id=str(data.get("parent_id") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            status=str(data.get("status") or ""),
        )


@dataclass(frozen=True)
class ChunkData:
    """Typed extraction of chunk fields from RuntimeEvent.data."""

    entity_id: str = ""
    entity_type: str = ""
    parent_id: str = ""
    tool_call_id: str = ""
    chunk_index: int = 0
    blocks: tuple[object, ...] = ()

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> ChunkData:
        data = event.data
        raw_blocks = data.get("blocks", [])
        blocks = tuple(raw_blocks) if isinstance(raw_blocks, list) else ()
        return cls(
            entity_id=str(data.get("entity_id") or ""),
            entity_type=str(data.get("entity_type") or ""),
            parent_id=str(data.get("parent_id") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            chunk_index=int(data.get("chunk_index") or 0),
            blocks=blocks,
        )


@dataclass(frozen=True)
class LLMFinishedData:
    """Typed extraction of llm.finished fields from RuntimeEvent.data."""

    model: str = ""
    usage: object = None
    cost: object = None
    duration_s: object = None
    tool_calls: tuple[object, ...] = ()
    message: object = None

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> LLMFinishedData:
        data = event.data
        raw_calls = data.get("tool_calls", [])
        tool_calls = tuple(raw_calls) if isinstance(raw_calls, list) else ()
        return cls(
            model=str(data.get("model") or ""),
            usage=data.get("usage"),
            cost=data.get("cost"),
            duration_s=data.get("duration_s"),
            tool_calls=tool_calls,
            message=data.get("message"),
        )


@dataclass
class ConversationStore:
    store: Store

    def _conn(self):
        return connections.get("default")

    async def create_conversation(
        self,
        *,
        conversation_id: str,
        actor_id: str,
    ) -> ConversationRecord:
        now = datetime.now(timezone.utc).isoformat()
        with self.store.db.activate():
            conn = self._conn()
            await conn.execute_query(
                """INSERT OR IGNORE INTO conversations
                   (conversation_id, actor_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                [conversation_id, actor_id, now, now],
            )
        existing = await self.get_conversation(conversation_id)
        if existing is None:
            raise RuntimeError(f"conversation {conversation_id!r} was not created")
        return existing

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> ConversationRecord | None:
        with self.store.db.activate():
            rows = await self._conn().execute_query_dict(
                "SELECT * FROM conversations WHERE conversation_id = ? LIMIT 1",
                [conversation_id],
            )
        if not rows:
            return None
        return _conversation_from_row(rows[0])

    async def list_conversations(
        self,
        *,
        actor_id: str | None = None,
    ) -> list[ConversationRecord]:
        where = ""
        params: list[object] = []
        if actor_id:
            where = "WHERE actor_id = ?"
            params.append(actor_id)
        with self.store.db.activate():
            rows = await self._conn().execute_query_dict(
                f"SELECT * FROM conversations {where} ORDER BY updated_at DESC",
                params,
            )
        return [_conversation_from_row(row) for row in rows]

    async def append_message(
        self,
        *,
        conversation_id: str,
        message_id: str,
        role: str,
        content: list[dict[str, object]],
        metadata: dict[str, object] | None = None,
        timestamp: int | None = None,
    ) -> ConversationMessageRecord:
        msg_ts = timestamp if timestamp is not None else int(time.time())
        raw_content = msgspec.json.encode(content).decode()
        raw_metadata = msgspec.json.encode(metadata or {}).decode()
        with self.store.db.activate():
            conn = self._conn()
            now = datetime.now(timezone.utc).isoformat()
            row_id = await conn.execute_insert(
                """INSERT INTO conversation_messages
                   (message_id, conversation_id, role, raw_content, metadata,
                    timestamp, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    message_id,
                    conversation_id,
                    role,
                    raw_content,
                    raw_metadata,
                    msg_ts,
                    now,
                ],
            )
            await conn.execute_query(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                [now, conversation_id],
            )
        return ConversationMessageRecord(
            id=row_id,
            message_id=message_id,
            conversation_id=conversation_id,
            role=role,
            raw_content=raw_content,
            metadata=metadata or {},
            timestamp=msg_ts,
        )

    async def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
    ) -> list[ConversationMessageRecord]:
        with self.store.db.activate():
            rows = await self._conn().execute_query_dict(
                """SELECT * FROM conversation_messages
                   WHERE conversation_id = ?
                   ORDER BY timestamp, id
                   LIMIT ?""",
                [conversation_id, limit],
            )
        return [_message_from_row(row) for row in rows]

    async def history(self, conversation_id: str) -> yuullm.History:
        messages = await self.list_messages(conversation_id, limit=1000)
        return [
            yuullm.Message(
                cast(Any, record.role),
                cast(Any, _decode_content(record.raw_content)),
            )
            for record in messages
            if record.role in {"user", "assistant", "system", "tool"}
        ]


@dataclass
class ConversationManager:
    store: ConversationStore
    actors: ActorManager
    _agent_to_conversation: dict[str, str] = field(default_factory=dict, init=False)
    _observed_actor_runtimes: dict[str, int] = field(default_factory=dict, init=False)
    _subscribers: dict[str, set[asyncio.Queue[AgentEvent]]] = field(
        default_factory=dict,
        init=False,
    )

    async def create_conversation(
        self,
        *,
        conversation_id: str,
        actor_id: str,
    ) -> ConversationRecord:
        return await self.store.create_conversation(
            conversation_id=conversation_id,
            actor_id=actor_id,
        )

    async def ensure_agent(self, conversation_id: str) -> dict[str, str]:
        conversation = await self._require_conversation(conversation_id)
        actor = await self._require_standard_actor(conversation.actor_id)
        self._observe_actor(conversation.actor_id, actor)
        agent = await actor.ensure_conversation_agent(
            conversation_id,
            await self.store.history(conversation_id),
        )
        self._agent_to_conversation[agent.agent_id] = conversation_id
        return {
            "conversation_id": conversation_id,
            "actor_id": conversation.actor_id,
            "agent_id": agent.agent_id,
            "agent_name": agent.agent_name,
        }

    async def send_message(
        self,
        *,
        conversation_id: str,
        content: list[dict[str, object]],
        message_id: str | None = None,
    ) -> ConversationMessageRecord:
        conversation = await self._require_conversation(conversation_id)
        actor = await self._require_standard_actor(conversation.actor_id)
        self._observe_actor(conversation.actor_id, actor)
        history = await self.store.history(conversation_id)
        agent = await actor.ensure_conversation_agent(conversation_id, history)
        self._agent_to_conversation[agent.agent_id] = conversation_id
        message_id = message_id or uuid.uuid4().hex
        record = await self.store.append_message(
            conversation_id=conversation_id,
            message_id=message_id,
            role="user",
            content=content,
            metadata={},
        )
        await actor.handle_conversation_message(
            conversation_id,
            yuullm.Message("user", cast(Any, content)),
            history,
        )
        return record

    async def subscribe_events(
        self,
        conversation_id: str,
    ) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        subscribers = self._subscribers.setdefault(conversation_id, set())
        subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(conversation_id, None)

    async def _require_conversation(self, conversation_id: str) -> ConversationRecord:
        conversation = await self.store.get_conversation(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id!r} does not exist")
        return conversation

    async def _require_standard_actor(self, actor_id: str):
        from yuubot.core.actors.impls.simple_loop import SimpleLoopActor

        actor = self.actors.running_actor(actor_id)
        if actor is None:
            actor = await self.actors.start_actor(actor_id)
        if not isinstance(actor, SimpleLoopActor):
            raise TypeError(f"actor {actor_id!r} does not support conversations")
        return actor

    def _observe_actor(self, actor_id: str, actor: Any) -> None:
        from yuubot.core.actors.impls.simple_loop import SimpleLoopActor

        if not isinstance(actor, SimpleLoopActor) or actor._runtime is None:
            return
        runtime = actor._runtime
        runtime_id = id(runtime)
        if self._observed_actor_runtimes.get(actor_id) == runtime_id:
            return
        runtime.stage.eventbus.subscribe(self._on_runtime_event)
        self._observed_actor_runtimes[actor_id] = runtime_id

    async def _on_runtime_event(self, event: RuntimeEvent) -> None:
        conversation_id = self._conversation_id_for_event(event)
        if conversation_id is None:
            return
        agent_event = await self._record_event(conversation_id, event)
        if agent_event is None:
            return
        for queue in tuple(self._subscribers.get(conversation_id, ())):
            await queue.put(agent_event)

    def _conversation_id_for_event(self, event: RuntimeEvent) -> str | None:
        if event.agent_id in self._agent_to_conversation:
            return self._agent_to_conversation[event.agent_id]
        identity = AgentEventIdentity.from_event(event)
        if identity.entity_id and identity.entity_id in self._agent_to_conversation:
            return self._agent_to_conversation[identity.entity_id]
        if identity.parent_id and identity.parent_id in self._agent_to_conversation:
            return self._agent_to_conversation[identity.parent_id]
        return None

    async def _record_event(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> AgentEvent | None:
        if event.name == "output.entity":
            return _agent_event(conversation_id, event, "entity", _entity_content(event))
        if event.name == "output.chunk":
            return _agent_event(
                conversation_id,
                event,
                _chunk_event_type(event),
                _chunk_content(event),
            )
        if event.name == "output.entity_end":
            return _agent_event(
                conversation_id,
                event,
                _entity_end_event_type(event),
                _entity_content(event),
            )
        if event.name == "llm.finished":
            finished = LLMFinishedData.from_event(event)
            message = finished.message
            if isinstance(message, yuullm.Message):
                content = _content_to_builtins(message.content)
                await self.store.append_message(
                    conversation_id=conversation_id,
                    message_id=uuid.uuid4().hex,
                    role=message.role,
                    content=content,
                    metadata=_event_metadata(event),
                    timestamp=int(event.timestamp),
                )
                return _agent_event(
                    conversation_id,
                    event,
                    "message",
                    {"role": message.role, "content": content},
                )
        if event.name in {"agent.turn.error", "budget.exceeded"}:
            return _agent_event(
                conversation_id,
                event,
                "error",
                _json_safe_dict(event.data),
            )
        return None

def _conversation_from_row(row: dict[str, Any]) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=str(row["conversation_id"]),
        actor_id=str(row["actor_id"]),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _message_from_row(row: dict[str, Any]) -> ConversationMessageRecord:
    return ConversationMessageRecord(
        id=int(row.get("id", 0)),
        message_id=str(row["message_id"]),
        conversation_id=str(row["conversation_id"]),
        role=str(row["role"]),
        raw_content=str(row["raw_content"]),
        metadata=_decode_metadata(row.get("metadata", {})),
        timestamp=int(row["timestamp"]),
        created_at=row.get("created_at"),
    )


def _agent_event(
    conversation_id: str,
    event: RuntimeEvent,
    event_type: str,
    content: dict[str, object],
) -> AgentEvent:
    return AgentEvent(
        conversation_id=conversation_id,
        agent_id=_agent_id_for_event(event),
        agent_name=event.agent_name,
        event_type=event_type,
        content=content,
        timestamp=event.timestamp,
    )


def _agent_id_for_event(event: RuntimeEvent) -> str:
    identity = AgentEventIdentity.from_event(event)
    if identity.agent_id:
        return identity.agent_id
    if identity.parent_id:
        return identity.parent_id
    return identity.entity_id


def _entity_content(event: RuntimeEvent) -> dict[str, object]:
    entity = EntityData.from_event(event)
    return _json_safe_dict({
        "entity_id": entity.entity_id or None,
        "entity_type": entity.entity_type or None,
        "parent_id": entity.parent_id or None,
        "tool_call_id": entity.tool_call_id or None,
        "status": entity.status or None,
    })


def _chunk_event_type(event: RuntimeEvent) -> str:
    if _is_tool_entity(event):
        return "tool_result"
    kinds = {_block_content_kind(block) for block in _event_blocks(event)}
    if "tool_call" in kinds:
        return "tool_call"
    if "thinking" in kinds:
        return "thinking"
    if "text" in kinds:
        return "text"
    return "output"


def _entity_end_event_type(event: RuntimeEvent) -> str:
    return "tool_result" if _is_tool_entity(event) else "entity_end"


def _chunk_content(event: RuntimeEvent) -> dict[str, object]:
    chunk = ChunkData.from_event(event)
    result: dict[str, object] = {}
    if chunk.entity_id:
        result["entity_id"] = chunk.entity_id
    if chunk.entity_type:
        result["entity_type"] = chunk.entity_type
    if chunk.parent_id:
        result["parent_id"] = chunk.parent_id
    if chunk.tool_call_id:
        result["tool_call_id"] = chunk.tool_call_id
    result["chunk_index"] = chunk.chunk_index
    if chunk.blocks:
        result["blocks"] = _json_safe(list(chunk.blocks))
    return result


def _event_blocks(event: RuntimeEvent) -> list[object]:
    chunk = ChunkData.from_event(event)
    return list(chunk.blocks)


def _is_tool_entity(event: RuntimeEvent) -> bool:
    entity = EntityData.from_event(event)
    return bool(entity.parent_id)


def _block_content_kind(block: object) -> str:
    raw = msgspec.to_builtins(block)
    if not isinstance(raw, dict):
        return "text"
    content = raw.get("content")
    if isinstance(content, str):
        return "text"
    if isinstance(content, dict):
        kind = content.get("type")
        if isinstance(kind, str):
            if "thinking" in kind:
                return "thinking"
            if kind == "tool_call":
                return "tool_call"
            if kind == "text":
                return "text"
            return kind
    return "output"


def _decode_content(raw_content: str) -> list[dict[str, object]]:
    return msgspec.json.decode(raw_content.encode())


def _decode_metadata(raw_metadata: object) -> dict[str, object]:
    if isinstance(raw_metadata, dict):
        return _json_safe_dict(raw_metadata)
    if isinstance(raw_metadata, str) and raw_metadata:
        return _json_safe_dict(msgspec.json.decode(raw_metadata.encode()))
    return {}


def _content_to_builtins(content: object) -> list[dict[str, object]]:
    value = msgspec.to_builtins(content)
    if not isinstance(value, list):
        return [{"type": "text", "text": str(value)}]
    result: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(_json_safe_dict(item))
        else:
            result.append({"type": "text", "text": str(item)})
    return result


def _event_metadata(event: RuntimeEvent) -> dict[str, object]:
    llm = LLMFinishedData.from_event(event)
    return _json_safe_dict({
        "model": llm.model or None,
        "usage": llm.usage,
        "cost": llm.cost,
        "duration_s": llm.duration_s,
        "tool_calls": list(llm.tool_calls) if llm.tool_calls else None,
    })


def _json_safe_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _json_safe(raw)
        for key, raw in value.items()
        if raw is not None
    }


def _json_safe(value: object) -> object:
    try:
        return msgspec.to_builtins(value)
    except TypeError:
        return repr(value)
