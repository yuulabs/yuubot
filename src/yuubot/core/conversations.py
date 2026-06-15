"""Conversation-mode storage and agent lifecycle coordination."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import msgspec
import yuullm
from yuuagents import Agent
from yuuagents.eventbus import RuntimeEvent

from yuubot.core.actors.impls.simple_loop import SimpleLoopActor
from yuubot.core.actors.manager import ActorManager
from yuubot.core.conversation_utils import (
    _agent_event,
    _chunk_content,
    _chunk_event_type,
    _content_to_builtins,
    _decode_content,
    _entity_content,
    _entity_end_event_type,
    _event_metadata,
    _json_safe_dict,
)
from yuubot.resources.records import (
    ConversationMessageRecord,
    ConversationRecord,
)
from yuubot.resources.store.models import ConversationMessageORM, ConversationORM
from yuubot.resources.store.protocol import to_builtins
from yuubot.resources.store.resource import Store


def _conversation_sort_key(record: ConversationRecord) -> tuple[float, str]:
    timestamp = record.updated_at or record.created_at
    if timestamp is None:
        return (0.0, record.conversation_id)
    return (timestamp.timestamp(), record.conversation_id)


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
            chunk_index=_int_value(data.get("chunk_index")),
            blocks=blocks,
        )


@dataclass(frozen=True)
class LLMFinishedData:
    """Typed extraction of llm.finished fields from RuntimeEvent.data."""

    model: str = ""
    usage: dict[str, object] | None = None
    cost: dict[str, object] | float | None = None
    duration_s: float | None = None
    tool_calls: tuple[dict[str, object], ...] = ()
    message: object | None = None

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> LLMFinishedData:
        data = event.data
        raw_calls = data.get("tool_calls", [])
        tool_calls = _tool_calls(raw_calls)
        return cls(
            model=str(data.get("model") or ""),
            usage=_dict_value(data.get("usage")),
            cost=_cost_value(data.get("cost")),
            duration_s=_float_value(data.get("duration_s")),
            tool_calls=tool_calls,
            message=data.get("message"),
        )


def _agent_id(agent: Agent) -> str:
    return agent.id


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float_value(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _dict_value(value: object) -> dict[str, object] | None:
    raw = msgspec.to_builtins(value)
    if not isinstance(raw, dict):
        return None
    return {str(key): item for key, item in raw.items()}


def _cost_value(value: object) -> dict[str, object] | float | None:
    if isinstance(value, int | float):
        return float(value)
    return _dict_value(value)


def _tool_calls(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    result: list[dict[str, object]] = []
    for item in value:
        data = _dict_value(item)
        if data is not None:
            result.append(data)
    return tuple(result)


@dataclass
class ConversationStore:
    store: Store

    async def create_conversation(
        self,
        *,
        conversation_id: str,
        actor_id: str,
    ) -> ConversationRecord:
        with self.store.db.activate():
            row, _created = await ConversationORM.get_or_create(
                conversation_id=conversation_id,
                defaults={"actor_id": actor_id},
            )
        if row is None:
            raise RuntimeError(f"conversation {conversation_id!r} was not created")
        return msgspec.convert(to_builtins(row), type=ConversationRecord, strict=False)

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> ConversationRecord | None:
        with self.store.db.activate():
            row = await ConversationORM.get_or_none(conversation_id=conversation_id)
        if row is None:
            return None
        return msgspec.convert(to_builtins(row), type=ConversationRecord, strict=False)

    async def list_conversations(
        self,
        *,
        actor_id: str | None = None,
    ) -> list[ConversationRecord]:
        with self.store.db.activate():
            if actor_id:
                rows = await ConversationORM.filter(actor_id=actor_id)
            else:
                rows = await ConversationORM.all()
        records = [
            msgspec.convert(to_builtins(r), type=ConversationRecord, strict=False)
            for r in rows
        ]
        return sorted(records, key=_conversation_sort_key, reverse=True)

    async def append_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        role: str,
        content: list[dict[str, object]],
        metadata: dict[str, object] | None = None,
        timestamp: int | None = None,
    ) -> ConversationMessageRecord:
        msg_ts = timestamp if timestamp is not None else int(time.time())
        with self.store.db.activate():
            row = await ConversationMessageORM.create(
                message_id=message_id,
                conversation_id=conversation_id,
                role=role,
                raw_content=msgspec.json.encode(content).decode(),
                metadata=metadata or {},
                timestamp=msg_ts,
            )
            now = datetime.now()
            await ConversationORM.filter(conversation_id=conversation_id).update(
                updated_at=now,
            )
        return msgspec.convert(
            to_builtins(row), type=ConversationMessageRecord, strict=False
        )

    async def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
    ) -> list[ConversationMessageRecord]:
        with self.store.db.activate():
            rows = (
                await ConversationMessageORM.filter(
                    conversation_id=conversation_id,
                )
                .order_by("timestamp", "id")
                .limit(limit)
            )
        return [
            msgspec.convert(
                to_builtins(r), type=ConversationMessageRecord, strict=False
            )
            for r in rows
        ]

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


# ---------------------------------------------------------------------------
# Event dispatch table for _record_event
# ---------------------------------------------------------------------------

_EventRecordHandler = Callable[
    ["ConversationManager", str, RuntimeEvent],
    "asyncio.Future[AgentEvent | None]",
]

_EVENT_DISPATCH: Mapping[str, str] = {
    "output.entity": "_handle_output_entity",
    "output.chunk": "_handle_output_chunk",
    "output.entity_end": "_handle_output_entity_end",
    "llm.finished": "_handle_llm_finished",
    "agent.turn.error": "_handle_error",
    "budget.exceeded": "_handle_error",
}


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
        agent_id = _agent_id(agent)
        self._agent_to_conversation[agent_id] = conversation_id
        return {
            "conversation_id": conversation_id,
            "actor_id": conversation.actor_id,
            "agent_id": agent_id,
            "agent_name": agent.name,
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
        self._agent_to_conversation[_agent_id(agent)] = conversation_id
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

    async def _require_standard_actor(self, actor_id: str) -> SimpleLoopActor:
        actor = self.actors.running_actor(actor_id)
        if actor is None:
            actor = await self.actors.start_actor(actor_id)
        if not isinstance(actor, SimpleLoopActor):
            raise TypeError(f"actor {actor_id!r} does not support conversations")
        return actor

    def _observe_actor(self, actor_id: str, actor: object) -> None:
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
        method_name = _EVENT_DISPATCH.get(event.name)
        if method_name is None:
            return None
        handler = getattr(self, method_name)
        return await handler(conversation_id, event)

    # -- Event handler methods (called via dispatch table) --

    async def _handle_output_entity(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> AgentEvent:
        return _agent_event(conversation_id, event, "entity", _entity_content(event))

    async def _handle_output_chunk(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> AgentEvent:
        return _agent_event(
            conversation_id,
            event,
            _chunk_event_type(event),
            _chunk_content(event),
        )

    async def _handle_output_entity_end(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> AgentEvent:
        return _agent_event(
            conversation_id,
            event,
            _entity_end_event_type(event),
            _entity_content(event),
        )

    async def _handle_llm_finished(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> AgentEvent | None:
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
        return None

    async def _handle_error(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> AgentEvent:
        return _agent_event(
            conversation_id,
            event,
            "error",
            _json_safe_dict(event.data),
        )
