"""Conversation-mode storage and agent lifecycle coordination."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import msgspec
import yuullm
from yuuagents import Agent, Budget, ProviderPoolSessionFactory
from yuuagents.core.eventbus import RuntimeEvent

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.assembly import YuuAgentsActorRuntime, start_yuuagents_actor
from yuubot.core.assembly._history_codec import (
    decode_prompt_item,
    encode_prompt_item,
)
from yuubot.core.bindings import AgentBinding, conversation_agent_binding
from yuubot.core.conversation_events import (
    ConversationFrontendEvent,
    ConversationSSEHeartbeat,
    ConversationSSEProjector,
    render_tool_output_final_text,
)
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.resources.records import (
    ActorRecord,
    CapabilitySetRecord,
    CharacterRecord,
    ConversationHistoryItemRecord,
    ConversationRecord,
    LLMBackendRecord,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.orm import from_orm
from yuubot.resources.store.models import (
    ActorORM,
    CapabilitySetORM,
    CharacterORM,
    ConversationHistoryItemORM,
    ConversationMessageORM,
    ConversationORM,
    LLMBackendORM,
)
from yuubot.resources.store.protocol import to_builtins
from yuubot.resources.store.resource import Store


def _conversation_sort_key(record: ConversationRecord) -> tuple[float, str]:
    timestamp = record.updated_at or record.created_at
    if timestamp is None:
        return (0.0, record.conversation_id)
    return (timestamp.timestamp(), record.conversation_id)


@dataclass(frozen=True)
class ConversationSendBinding:
    """Binding fields carried on the first send request body.

    ``actor_id`` is required on first send; the remaining fields default
    from the actor's ``default_*`` records when omitted.
    """

    conversation_id: str
    actor_id: str
    character_id: str = ""
    capability_set_id: str = ""
    llm_backend_id: str = ""
    model: str = ""


@dataclass
class ConversationBindingConflict(Exception):
    conversation: ConversationRecord

    def __str__(self) -> str:
        return (
            f"conversation {self.conversation.conversation_id!r} already has "
            "messages and is bound to a different actor/binding"
        )


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


def _cost_total(value: dict[str, object] | float | None) -> float | None:
    """Extract the ``total_cost`` USD figure from an ``llm.finished`` cost payload.

    The runtime emits ``cost`` as a ``yuullm.Cost`` msgspec.Struct; by the
    time it reaches ``LLMFinishedData`` it has been normalised to a dict
    (``{"total_cost": float, ...}``). A bare ``float`` (legacy / scalar
    cost) is returned as-is. ``None`` (no usage / no pricing) → ``None``.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    total = value.get("total_cost")
    if isinstance(total, int | float):
        return float(total)
    return None


def _tool_calls(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    result: list[dict[str, object]] = []
    for item in value:
        data = _dict_value(item)
        if data is not None:
            result.append(data)
    return tuple(result)


def _conversation_title_from_first_turn(
    user_message: yuullm.Message,
    assistant_message: yuullm.Message,
) -> str:
    user_text = yuullm.render_message_text(user_message).strip()
    assistant_text = yuullm.render_message_text(assistant_message).strip()
    text = user_text or assistant_text
    if not text:
        return ""
    title = " ".join(text.split())
    if len(title) <= 80:
        return title
    return title[:77].rstrip() + "..."


@dataclass
class ConversationStore:
    store: Store

    async def create_conversation_row(
        self,
        *,
        conversation_id: str,
        character: CharacterRecord,
        capability_set: CapabilitySetRecord,
        llm_backend: LLMBackendRecord,
        model: str,
        llm_options: YuuAgentLLMOptions,
        budget: YuuAgentBudget,
        actor_id: str,
        title: str = "",
        reply_address: str = "",
        metadata: dict[str, object] | None = None,
    ) -> ConversationRecord:
        with self.store.db.activate():
            row = await ConversationORM.create(
                conversation_id=conversation_id,
                character_id=character.id,
                capability_set_id=capability_set.id,
                llm_backend_id=llm_backend.id,
                model=model,
                llm_options=msgspec.to_builtins(llm_options),
                budget=msgspec.to_builtins(budget),
                actor_id=actor_id,
                title=title,
                reply_address=reply_address,
                metadata=metadata or {},
            )
            row = await ConversationORM.get(
                conversation_id=conversation_id,
            ).select_related(
                "character",
                "capability_set",
                "llm_backend",
            )
            return await from_orm(row, ConversationRecord)

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> ConversationRecord | None:
        with self.store.db.activate():
            row = await ConversationORM.get_or_none(
                conversation_id=conversation_id,
            ).select_related("character", "capability_set", "llm_backend")
            if row is None:
                return None
            return await from_orm(row, ConversationRecord)

    async def conversation_exists(self, conversation_id: str) -> bool:
        with self.store.db.activate():
            return await ConversationORM.filter(
                conversation_id=conversation_id,
            ).exists()

    async def list_conversations(
        self,
        *,
        actor_id: str | None = None,
    ) -> list[ConversationRecord]:
        with self.store.db.activate():
            if actor_id:
                query = ConversationORM.filter(actor_id=actor_id)
            else:
                query = ConversationORM.all()
            rows = await query.select_related(
                "character",
                "capability_set",
                "llm_backend",
            )
            records = [await from_orm(r, ConversationRecord) for r in rows]
        return sorted(records, key=_conversation_sort_key, reverse=True)

    async def update_title_if_empty(
        self,
        conversation_id: str,
        title: str,
    ) -> bool:
        if not title:
            return False
        with self.store.db.activate():
            updated = await ConversationORM.filter(
                conversation_id=conversation_id,
                title="",
            ).update(title=title)
        return updated > 0

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self.store.transaction():
            with self.store.db.activate():
                exists = await ConversationORM.filter(
                    conversation_id=conversation_id,
                ).exists()
                if not exists:
                    return False
                await ConversationMessageORM.filter(
                    conversation_id=conversation_id,
                ).delete()
                await ConversationHistoryItemORM.filter(
                    conversation_id=conversation_id,
                ).delete()
                await ConversationORM.filter(
                    conversation_id=conversation_id,
                ).delete()
        return True

    # ── Ordered history items (canonical conversation state) ──────────

    async def append_history_item(
        self,
        conversation_id: str,
        item: yuullm.PromptItem,
    ) -> ConversationHistoryItemRecord:
        item_kind, item_json = encode_prompt_item(item)
        with self.store.db.activate():
            row = await ConversationHistoryItemORM.create(
                conversation_id=conversation_id,
                item_kind=item_kind,
                item_json=item_json,
            )
            now = datetime.now()
            await ConversationORM.filter(
                conversation_id=conversation_id,
            ).update(updated_at=now)
        return msgspec.convert(
            to_builtins(row), type=ConversationHistoryItemRecord, strict=False
        )

    async def append_history_items(
        self,
        conversation_id: str,
        items: list[yuullm.PromptItem],
    ) -> list[ConversationHistoryItemRecord]:
        if not items:
            return []
        encoded = [encode_prompt_item(item) for item in items]
        with self.store.db.activate():
            async with self.store.transaction():
                rows: list[ConversationHistoryItemRecord] = []
                for item_kind, item_json in encoded:
                    row = await ConversationHistoryItemORM.create(
                        conversation_id=conversation_id,
                        item_kind=item_kind,
                        item_json=item_json,
                    )
                    rows.append(
                        msgspec.convert(
                            to_builtins(row),
                            type=ConversationHistoryItemRecord,
                            strict=False,
                        )
                    )
                now = datetime.now()
                await ConversationORM.filter(
                    conversation_id=conversation_id,
                ).update(updated_at=now)
        return rows

    async def list_history_items(
        self,
        conversation_id: str,
    ) -> list[ConversationHistoryItemRecord]:
        with self.store.db.activate():
            rows = (
                await ConversationHistoryItemORM.filter(
                    conversation_id=conversation_id,
                )
                .order_by("id")
                .limit(1000)
            )
        return [
            msgspec.convert(
                to_builtins(r), type=ConversationHistoryItemRecord, strict=False
            )
            for r in rows
        ]

    async def history(self, conversation_id: str) -> yuullm.History:
        rows = await self.list_history_items(conversation_id)
        return [decode_prompt_item(row.item_kind, row.item_json) for row in rows]


_PROJECTED_RUNTIME_EVENTS = {
    "output.chunk",
    "agent.turn.error",
    "agent.turn_started",
    "agent.turn_completed",
    "budget.exceeded",
}


@dataclass
class ConversationManager:
    store: ConversationStore
    repository: ResourceRepository
    yuuagents_config: YuuAgentsConfig
    python_sessions: ActorPythonSessionFactory
    llm_session_factory_factory: Callable[[AgentBinding], ProviderPoolSessionFactory | None]
    trace_context: YuubotTraceContextProvider | None = None
    workspace_root: Path = field(
        default_factory=lambda: Path("~/.yuubot/workspace").expanduser()
    )
    _runtimes: dict[str, YuuAgentsActorRuntime] = field(default_factory=dict, init=False)
    _agent_to_conversation: dict[str, str] = field(default_factory=dict, init=False)
    _observed_runtimes: dict[str, int] = field(default_factory=dict, init=False)
    _subscribers: dict[
        str,
        set[asyncio.Queue[ConversationFrontendEvent | ConversationSSEHeartbeat]],
    ] = field(
        default_factory=dict,
        init=False,
    )
    # One in-flight turn task per conversation. A conversation can only have
    # one active turn at a time. The frontend disables the Send button during
    # generation (replaced by the Stop button), so a second ``send_message``
    # while a turn is in flight is not normally reachable from the UI. If one
    # does arrive, ``send_message`` defensively awaits the existing task
    # before starting a new one (no server-side queue is needed — the input
    # box itself is the buffer).
    _in_flight_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)
    # Per-conversation cancel event. ``cancel_turn`` sets the event AND calls
    # ``task.cancel()``; the agent loop checks it once at the top of each
    # ``while not agent.done`` iteration as a single-point safety net (closes
    # the window where the loop is between awaits and ``task.cancel()``'s
    # scheduled CancelledError has not yet been delivered).
    _cancel_events: dict[str, asyncio.Event] = field(default_factory=dict, init=False)
    _sse_projector: ConversationSSEProjector = field(
        default_factory=ConversationSSEProjector,
        init=False,
    )

    async def send_message(
        self,
        *,
        conversation_id: str,
        text: str,
        binding: ConversationSendBinding | None = None,
        message_id: str | None = None,
    ) -> tuple[ConversationRecord, str]:
        """Persist a user Message and run the conversation turn.

        ``binding`` carries first-send binding fields (``actor_id`` etc.).
        On the first real send it is required and the conversation row is
        created from it; on subsequent sends the persisted binding is the
        authority and any conflicting ``binding.actor_id`` raises
        :class:`ConversationBindingConflict`.

        Returns the persisted ``ConversationRecord`` and the user message id.
        The turn itself runs on a background task — the method returns
        before the turn completes, mirroring the prior 202 semantics.
        """
        exists = await self.store.conversation_exists(conversation_id)
        if exists:
            conversation = await self._require_conversation(conversation_id)
            self._check_subsequent_send_binding(conversation, binding)
        else:
            conversation = await self._create_first_send_conversation(
                conversation_id=conversation_id,
                binding=binding,
            )

        runtime = await self._runtime_for(conversation)

        # Cache hit on the in-memory agent short-circuits the DB history
        # read on the hot path. Cache miss (restart / idle expiry) and
        # first-send both branch inside runtime.ensure_conversation_agent.
        if exists and runtime.conversation_agents.get(conversation_id) is None:
            history = await self.store.history(conversation_id)
        else:
            history = []

        agent = await runtime.ensure_conversation_agent(conversation_id, history)
        self._agent_to_conversation[_agent_id(agent)] = conversation_id

        # Persist the freshly-built prompt prefix on the first-send path
        # (prefix lives inside agent.history now). Persisted before the
        # user Message so ordering stays [tool_specs?, system, user, ...].
        if not exists:
            prefix = list(agent.history)
            if prefix:
                await self.store.append_history_items(conversation_id, prefix)

        message_id = message_id or uuid.uuid4().hex
        user_message = yuullm.user(text)
        await self.store.append_history_item(conversation_id, user_message)

        # Defensive: if a previous turn task is somehow still live (the
        # frontend contract replaces Send with Stop during generation, so
        # this path is not normally reachable), wait for it before starting
        # a new one. Avoids two concurrent turn tasks racing on the same
        # agent/history. The input box itself is the buffer — no server-side
        # queue is needed.
        existing = self._in_flight_tasks.get(conversation_id)
        if existing is not None and not existing.done():
            with suppress(asyncio.CancelledError, Exception):
                await existing

        self._start_turn_task(
            conversation_id=conversation_id,
            runtime=runtime,
            message=user_message,
        )
        return conversation, message_id

    def _check_subsequent_send_binding(
        self,
        conversation: ConversationRecord,
        binding: ConversationSendBinding | None,
    ) -> None:
        if binding is None:
            return
        supplied_actor = (binding.actor_id or "").strip()
        if supplied_actor and supplied_actor != conversation.actor_id:
            raise ConversationBindingConflict(conversation=conversation)

    async def _create_first_send_conversation(
        self,
        *,
        conversation_id: str,
        binding: ConversationSendBinding | None,
    ) -> ConversationRecord:
        if binding is None or not binding.actor_id.strip():
            raise LookupError(
                f"first send for conversation {conversation_id!r} requires actor_id"
            )
        actor = await self._active_actor(binding.actor_id.strip())
        character_id = binding.character_id.strip() or actor.default_character.id
        capability_set_id = (
            binding.capability_set_id.strip() or actor.capability_set.id
        )
        llm_backend_id = binding.llm_backend_id.strip() or actor.default_llm_backend.id
        model = binding.model.strip() or actor.default_model

        if character_id != actor.default_character.id:
            character = await self._require_character(character_id)
        else:
            character = actor.default_character
        if capability_set_id != actor.capability_set.id:
            capability_set = await self._require_capability_set(capability_set_id)
        else:
            capability_set = actor.capability_set
        if llm_backend_id != actor.default_llm_backend.id:
            llm_backend = await self._require_llm_backend(llm_backend_id)
        else:
            llm_backend = actor.default_llm_backend

        return await self.store.create_conversation_row(
            conversation_id=conversation_id,
            character=character,
            capability_set=capability_set,
            llm_backend=llm_backend,
            model=model,
            llm_options=actor.default_llm_options,
            budget=actor.default_budget,
            actor_id=actor.id,
            title="",
            reply_address="",
            metadata={},
        )

    def _start_turn_task(
        self,
        *,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
        message: yuullm.Message,
    ) -> None:
        cancel_event = asyncio.Event()
        self._cancel_events[conversation_id] = cancel_event

        task = asyncio.create_task(
            self._run_turn_task(
                conversation_id=conversation_id,
                runtime=runtime,
                message=message,
                cancel_event=cancel_event,
            )
        )
        self._in_flight_tasks[conversation_id] = task

        def _cleanup(_t: asyncio.Task[None], cid: str = conversation_id) -> None:
            if self._in_flight_tasks.get(cid) is _t:
                self._in_flight_tasks.pop(cid, None)
            # ``_cancel_events`` is turn-scoped (the event for *this* turn's
            # single-point safety trip); pop on task-done.
            self._cancel_events.pop(cid, None)

        task.add_done_callback(_cleanup)

    async def _run_turn_task(
        self,
        *,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
        message: yuullm.Message,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        try:
            await runtime.handle_conversation_message(
                conversation_id,
                message,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            # The agent loop's own CancelledError handler already ran
            # (flushed the reporter, cancelled tools, synthesised results).
            # The loop is the sole emitter of ``agent.turn_completed``;
            # ``cancel_turn`` does NOT synthesise it. Re-raise so asyncio
            # marks the task cancelled.
            raise
        except Exception as exc:
            event = RuntimeEvent(
                name="agent.turn.error",
                agent_id="",
                agent_name="",
                timestamp=time.time(),
                data={"error": str(exc)},
            )
            frontend_event = self._sse_projector.error(
                conversation_id,
                event,
                str(exc),
            )
            for queue in tuple(self._subscribers.get(conversation_id, ())):
                await queue.put(frontend_event)

    async def cancel_turn(self, conversation_id: str) -> dict[str, bool]:
        """Cancel the in-flight turn for ``conversation_id`` (Stop button).

        Sets the single-point safety ``cancel_event`` and calls
        ``task.cancel()``. The loop's own CancelledError handler then:
        flushes the reporter, cancels running tools, and synthesises
        ``[cancelled]`` tool results so the in-memory history stays legal.
        The loop ``break``s out of the turn (always — there is no
        ``continue`` branch anymore; the queue mechanism is gone), so the
        loop's terminal path emits ``agent.turn_completed`` via the normal
        loop-exit path. ``cancel_turn`` itself does NOT synthesise
        ``turn_completed`` — the loop is the sole emitter.

        Awaits the cancelled task before returning — the HTTP response is
        the "stop receipt" the frontend waits for (the button transitions
        from "stopping" back to "Send" only after the CancelledError handler
        has completed and the loop has emitted ``turn_completed``).

        Returns ``{"cancelled": bool}`` — ``True`` when a turn task was
        actually signalled and awaited to completion, ``False`` when there
        was no live task to cancel.
        """
        task = self._in_flight_tasks.get(conversation_id)
        if task is None or task.done():
            return {"cancelled": False}

        # Single-point safety trip: the loop checks ``cancel_event.is_set()``
        # once at the top of each iteration; if true it raises
        # CancelledError into the handler below. This closes the window
        # where the loop is between awaits and ``task.cancel()``'s
        # scheduled CancelledError has not yet been delivered.
        event = self._cancel_events.get(conversation_id)
        if event is not None:
            event.set()

        # Break mid-LLM-stream awaits. If the loop already exited between
        # the done() check and now, task.cancel() is a no-op on the
        # completing task; we report ``cancelled=False`` in that case.
        scheduled = task.cancel()
        if not scheduled:
            return {"cancelled": False}

        # Await the task so the CancelledError handler completes (flush +
        # cancel tools + synthesise [cancelled] tool_results) before the
        # HTTP response returns. This is the "stop receipt" the frontend
        # waits for. The loop's own exit path emits ``agent.turn_completed``
        # → SSE ``turn_completed``; ``cancel_turn`` does NOT synthesise it.
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # Loop's error path already emitted an error SSE event; suppress
            # so the HTTP receipt still returns 200 (the frontend drops
            # ``isSending`` via the error SSE or ``turn_completed``).
            pass

        return {"cancelled": True}

    async def delete_conversation(self, conversation_id: str) -> bool:
        exists = await self.store.conversation_exists(conversation_id)
        if not exists:
            return False
        await self.cancel_turn(conversation_id)
        self.drop_cached_conversation_agent(conversation_id)
        deleted = await self.store.delete_conversation(conversation_id)
        if deleted:
            for agent_id, stored_id in list(self._agent_to_conversation.items()):
                if stored_id == conversation_id:
                    self._agent_to_conversation.pop(agent_id, None)
            self._observed_runtimes.pop(conversation_id, None)
            self._cancel_events.pop(conversation_id, None)
        return deleted

    async def subscribe_events(
        self,
        conversation_id: str,
        *,
        heartbeat_interval: float = 25.0,
    ) -> AsyncIterator[ConversationFrontendEvent | ConversationSSEHeartbeat]:
        """Subscribe to the long-lived SSE stream for one conversation.

        The stream stays open across turns. ``agent.turn_completed`` is
        projected to a named ``turn_completed`` event that the frontend
        listens for; it does **not** close the stream. Closing on each
        turn was the regression that dropped the second turn's events:

        ```
        mount → connectSse (stream open)
        User msg 1 → daemon emits transcript_delta → turn_completed
          → stream closed by daemon → EventSource onerror → frontend
            tears down the EventSource  ← completion signalled via TCP close
        User msg 2 → handleSend does not reopen stream
          → daemon emits transcript_delta into an empty subscriber set
          → events dropped → "Waiting for response…" hangs
        ```

        A heartbeat (: heartbeat\\n\\n comment frame, rendered by the
        daemon SSE handler) is yielded whenever no event arrives within
        ``heartbeat_interval`` seconds — short enough to keep any idle
        HTTP hop or middlebox from closing the connection, long enough
        to be negligible overhead (default 25s).
        """
        queue: asyncio.Queue[ConversationFrontendEvent | ConversationSSEHeartbeat] = asyncio.Queue()
        subscribers = self._subscribers.setdefault(conversation_id, set())
        subscribers.add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    yield ConversationSSEHeartbeat(conversation_id)
                    continue
                yield event
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(conversation_id, None)

    def drop_cached_conversation_agent(self, conversation_id: str) -> bool:
        """Evict the in-memory runtime+agent cache for ``conversation_id``.

        Forces the next :meth:`send_message` to fall into the restart
        branch: a fresh runtime is built and ``ensure_conversation_agent``
        reads ``store.history`` to restore the persisted prefix.

        Used by tests to simulate a daemon-restart cache drop without
        losing the on-disk history. Returns ``True`` if a cached runtime
        was evicted.
        """
        runtime = self._runtimes.pop(conversation_id, None)
        if runtime is None:
            return False
        # Drop this conversation's agent-to-conversation index entries;
        # the next send re-registers them via ensure_conversation_agent.
        for agent_id, stored_id in list(self._agent_to_conversation.items()):
            if stored_id == conversation_id:
                self._agent_to_conversation.pop(agent_id, None)
        self._observed_runtimes.pop(conversation_id, None)
        return True

    async def _require_conversation(self, conversation_id: str) -> ConversationRecord:
        conversation = await self.store.get_conversation(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id!r} does not exist")
        return conversation

    async def _active_actor(self, actor_id: str) -> ActorRecord:
        actor = await self.repository.get(ActorORM, actor_id)
        if actor is None or not actor.enabled:
            raise LookupError(f"active actor {actor_id!r} does not exist")
        return actor

    async def _require_character(self, character_id: str) -> CharacterRecord:
        character = await self.repository.get(CharacterORM, character_id)
        if character is None:
            raise LookupError(f"character {character_id!r} does not exist")
        return character

    async def _require_capability_set(self, capability_set_id: str) -> CapabilitySetRecord:
        capability_set = await self.repository.get(CapabilitySetORM, capability_set_id)
        if capability_set is None:
            raise LookupError(f"capability set {capability_set_id!r} does not exist")
        return capability_set

    async def _require_llm_backend(self, llm_backend_id: str) -> LLMBackendRecord:
        llm_backend = await self.repository.get(LLMBackendORM, llm_backend_id)
        if llm_backend is None:
            raise LookupError(f"llm backend {llm_backend_id!r} does not exist")
        return llm_backend

    def _resolve_workspace_path(self, relative_name: str | None) -> Path | None:
        """Resolve a CapabilitySet's workspace_path (relative name) under workspace_root.

        Returns None if relative_name is empty — caller handles None by not
        setting workspace_path on the binding (facade will be None, no workspace needed).
        """
        if not relative_name or not relative_name.strip():
            return None
        root = self.workspace_root.expanduser().resolve()
        path = (root / relative_name).resolve()
        if not path.is_relative_to(root):
            raise ValueError(
                f"workspace_path {relative_name!r} escapes workspace root {root}"
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _runtime_for(self, conversation: ConversationRecord) -> YuuAgentsActorRuntime:
        runtime = self._runtimes.get(conversation.conversation_id)
        if runtime is not None:
            return runtime
        workspace_path = self._resolve_workspace_path(conversation.capability_set.workspace_path)
        binding = conversation_agent_binding(conversation, workspace_path=workspace_path)
        facade = None
        if binding.workspace_path is not None:
            facade = await self.python_sessions.bind_facade(
                binding,
                mailbox_id=f"conversation:{conversation.conversation_id}",
            )
        llm_session_factory = self.llm_session_factory_factory(binding)
        runtime = start_yuuagents_actor(
            binding,
            yuuagents_config=self.yuuagents_config,
            facade=facade,
            llm_session_factory=llm_session_factory,
            trace_context=self.trace_context,
        )
        self._runtimes[conversation.conversation_id] = runtime
        self._observe_runtime(conversation.conversation_id, runtime)

        # Inject real conversation_id into trace context so spans link
        # to yuubot conversation records (not synthetic uuid5).
        if self.trace_context is not None:
            definition_name = runtime.conversation_definition.name
            self.trace_context.register(
                f"{definition_name}:conversation:{conversation.conversation_id}",
                character_name=conversation.character.name,
                model=binding.llm.model,
                conversation_id=conversation.conversation_id,
            )

        return runtime

    def _observe_runtime(
        self,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
    ) -> None:
        runtime_id = id(runtime)
        if self._observed_runtimes.get(conversation_id) == runtime_id:
            return
        runtime.stage.eventbus.subscribe(self._on_runtime_event)
        self._observed_runtimes[conversation_id] = runtime_id

    async def _on_runtime_event(self, event: RuntimeEvent) -> None:
        conversation_id = self._conversation_id_for_event(event)
        if conversation_id is None:
            return
        frontend_events = await self._record_event(conversation_id, event)
        if not frontend_events:
            return
        for queue in tuple(self._subscribers.get(conversation_id, ())):
            for frontend_event in frontend_events:
                await queue.put(frontend_event)

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
    ) -> list[ConversationFrontendEvent]:
        if event.name in _PROJECTED_RUNTIME_EVENTS:
            return self._sse_projector.project_runtime_event(conversation_id, event)
        if event.name == "llm.finished":
            await self._handle_llm_finished(conversation_id, event)
            return self._cost_update_events(conversation_id, event)
        if event.name == "tool.result_appended":
            return await self._handle_tool_result(conversation_id, event)
        return []

    def _cost_update_events(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        """Project a ``cost_update`` SSE event from an ``llm.finished`` event.

        Only emitted when the LLM step produced a cost. When the in-memory
        ``Budget`` is available (normal path), ``total_cost`` is the
        running cumulative USD spend held by the agent's Budget; when the
        budget lookup misses (cold path after a daemon restart where the
        agent was rebuilt but the budget was reset), ``total_cost`` falls
        back to the per-call ``turn_cost`` so the frontend still gets a
        non-zero "$X spent" frame.
        """
        finished = LLMFinishedData.from_event(event)
        turn_cost = _cost_total(finished.cost)
        if turn_cost is None:
            return []
        budget = self._budget_for_event(event)
        total_cost = (
            float(budget.usage.get("usd", 0.0)) if budget is not None else turn_cost
        )
        return [
            self._sse_projector.cost_update(
                conversation_id,
                event,
                turn_cost=turn_cost,
                total_cost=total_cost,
            )
        ]

    def _budget_for_event(self, event: RuntimeEvent) -> Budget | None:
        """Look up the in-memory ``Budget`` for the agent that emitted ``event``.

        ``Budget`` is owned by ``YuuAgentsActorRuntime._agent_budgets`` (the
        orchestrator, not the Agent). The ``ConversationManager`` creates
        each runtime and tracks it by conversation_id, so the lookup goes
        conversation_id → runtime → ``budget_for_agent(agent_id)``. Returns
        ``None`` when no runtime is cached (e.g. event arrived after the
        runtime was evicted) — callers fall back to per-turn cost.
        """
        agent_id = event.agent_id or ""
        if not agent_id:
            return None
        conversation_id = self._agent_to_conversation.get(agent_id)
        if conversation_id is None:
            return None
        runtime = self._runtimes.get(conversation_id)
        if runtime is None:
            return None
        return runtime.budget_for_agent(agent_id)


    async def _handle_llm_finished(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> None:
        finished = LLMFinishedData.from_event(event)
        message = finished.message
        if isinstance(message, yuullm.Message):
            await self.store.append_history_item(conversation_id, message)
            await self._set_title_from_first_turn(conversation_id)

    async def _set_title_from_first_turn(self, conversation_id: str) -> None:
        rows = await self.store.list_history_items(conversation_id)
        user_message: yuullm.Message | None = None
        assistant_message: yuullm.Message | None = None
        for row in rows:
            try:
                decoded = decode_prompt_item(row.item_kind, row.item_json)
            except ValueError:
                continue
            if not isinstance(decoded, yuullm.Message):
                continue
            if decoded.role == "user" and user_message is None:
                user_message = decoded
                continue
            if decoded.role == "assistant" and assistant_message is None:
                assistant_message = decoded
        if user_message is None or assistant_message is None:
            return
        title = _conversation_title_from_first_turn(
            user_message,
            assistant_message,
        )
        await self.store.update_title_if_empty(conversation_id, title)

    async def _handle_tool_result(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        data = event.data
        tool_call_id = str(data.get("tool_call_id") or "")
        result = render_tool_output_final_text(str(data.get("result") or ""))
        tool_name = str(data.get("tool_name") or "")
        status = str(data.get("status") or "completed")
        _ = status

        # Persist the canonical yuullm.tool(...) Message shape: role="tool",
        # content=[{type:"tool_result", tool_call_id, content}]. The SSE
        # projector continues to consume the decorated fields from the
        # runtime event for frontend rendering; only the persisted item
        # uses the canonical shape.
        await self.store.append_history_item(
            conversation_id,
            yuullm.tool(tool_call_id, result),
        )

        missing = self._sse_projector.missing_tool_result_delta(
            conversation_id,
            event,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            text=result,
        )
        return [] if missing is None else [missing]


def _turn_id(event: RuntimeEvent) -> str:
    data = event.data
    return str(data.get("turn_id") or data.get("task_id") or event.agent_id or "")
