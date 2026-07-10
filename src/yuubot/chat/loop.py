"""Conversation: the business orchestration loop, and its manager.

Concurrency contract
--------------------
* One ``run_loop`` at a time per Conversation. A second concurrent call raises
  ``ConversationBusy``; callers (WebSocket facade, actors) surface it as a busy
  signal instead of queueing.
* ``run_loop`` marks the conversation running and clears ``stop_event`` in one
  synchronous step (no await in between), and ``interrupt`` only sets the event
  while a loop is running. An interrupt therefore either lands in the active
  loop or reports ``False``; it can never be silently swallowed by a later
  ``clear()``.
* ``ConversationManager.get_or_create`` serializes creation so two concurrent
  requests for the same conversation id share one Conversation object.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Protocol

import msgspec
from attrs import define, field

from .harness import Harness, HarnessConfig
from .history import PREFIX_KINDS, HistoryHelper
from .titles import title_from_user_message
from ..actor.prompt import SessionMode, augment_user_message
from ..llm.gateway import RequestMetadata, StreamClient
from ..domain.messages import (
    ContentItem,
    ConversationContext,
    GenOutput,
    GenToolCall,
    HistoryItem,
    InputMessage,
    text_content,
)
from ..domain.stream import StreamEvent, StreamStop, extract_tool_calls, merge
from ..runtime.event_payloads import (
    ConversationUsagePayload,
    ConversationInputPayload,
)
from ..runtime.tasks import skip_conversation_task_deliveries
from ..runtime.turn_limits import TurnIdentity
from ..util.asyncio_ import BackgroundSweeper
from ..util.stream import stream_stop_from

if TYPE_CHECKING:
    from ..runtime.core import Runtime

_log = logging.getLogger(__name__)


class ConversationBlocked(RuntimeError):
    """The LLM stopped for a reason that is neither completion nor tool use."""


class ConversationBusy(RuntimeError):
    """A run_loop is already active for this conversation."""


StreamCallback = Callable[[StreamEvent], Awaitable[None]]

PYTHON_RESET_NOTICE = (
    "The previous execute_python session has been reset; variables, imports, "
    "and side effects from that session are no longer available."
)


@define(frozen=True)
class ConversationSnapshot:
    prefix: list[dict[str, object]]
    living_chunks: list[StreamEvent]
    version: int


class ConversationSubscriber(Protocol):
    async def on_snapshot(self, conversation_id: str, snapshot: ConversationSnapshot) -> None: ...

    async def on_delta(self, conversation_id: str, chunk: StreamEvent, version: int) -> None: ...

    async def on_commit(
        self,
        conversation_id: str,
        append: list[dict[str, object]],
        continues: bool,
        version: int,
    ) -> None: ...

    async def on_error(self, conversation_id: str, error: str) -> None: ...


@define
class Conversation:
    id: str
    context: ConversationContext
    history: HistoryHelper
    stream_client: StreamClient
    harness_config: HarnessConfig
    runtime: "Runtime"
    prefix: list[dict[str, object]] = field(factory=list)
    living_chunks: list[StreamEvent] = field(factory=list, init=False)
    version: int = field(default=0, init=False)
    stop_event: asyncio.Event = field(factory=asyncio.Event)
    _running: bool = field(default=False, init=False)
    _pending_task_deliveries: list[str] = field(factory=list, init=False)
    _task_delivery_suppressed_until: float | None = field(default=None, init=False)
    _subscribers: set[ConversationSubscriber] = field(factory=set, init=False)
    _pending_deltas: list[asyncio.Task[None]] = field(factory=list, init=False)
    _state_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    last_stop: StreamStop | None = field(default=None, init=False)

    @property
    def running(self) -> bool:
        return self._running

    async def run_loop(
        self,
        input: InputMessage,
        on_event: StreamCallback | None = None,
        session_mode: SessionMode | None = None,
    ) -> list[GenOutput]:
        self._enter_run()
        turn_token = ""
        try:
            await self._mark_status("active")
            if input.role == "user":
                turn_id = uuid.uuid4().hex
                turn_token = self.runtime.turn_limits.open(
                    TurnIdentity(
                        self.context.actor,
                        self.id,
                        turn_id,
                        str(self.context.otel.get("trace_id") or self.id),
                    )
                )
                self.context.rpc["turn_id"] = turn_id
                self.context.rpc["turn_token"] = turn_token
                self.runtime.allow_task_deliveries(self.id)
                if session_mode is not None:
                    input = augment_user_message(input, session_mode)
                await self.runtime.state.set_conversation_title_if_empty(
                    self.id,
                    title_from_user_message(input),
                )
            if self._needs_python_reset_notice():
                await self._emit_commit(
                    [InputMessage("developer", "yuubot", [ContentItem("text", PYTHON_RESET_NOTICE)])],
                    True,
                )
            await self._emit_commit([input], True)
            self.runtime.emit(
                ConversationInputPayload(self.id, msgspec.to_builtins(input.content))
            )
            return await self._run_loop(on_event)
        except asyncio.CancelledError:
            await self._mark_status("interrupted")
            raise
        finally:
            if turn_token:
                self.runtime.turn_limits.close(turn_token)
                self.context.rpc.pop("turn_id", None)
                self.context.rpc.pop("turn_token", None)
            await self._exit_run()

    async def run_continuation(self, on_event: StreamCallback | None = None) -> list[GenOutput]:
        self._enter_run()
        try:
            await self._mark_status("active")
            return await self._run_loop(on_event)
        finally:
            await self._exit_run()

    async def append_developer_notice(
        self,
        text: str,
        name: str = "yuubot",
        on_event: StreamCallback | None = None,
    ) -> list[GenOutput]:
        self._enter_run()
        try:
            await self._mark_status("active")
            await self._emit_commit([InputMessage("developer", name, text_content(text))], True)
            return await self._run_loop(on_event)
        finally:
            await self._exit_run()

    async def append_items(self, items: Sequence[HistoryItem]) -> None:
        if self._running:
            raise ConversationBusy(self.id)
        await self._emit_commit(items, False)

    def interrupt(self) -> bool:
        if not self._running:
            return False
        self.runtime.suppress_task_deliveries(self.id)
        self.stop_event.set()
        return True

    async def snapshot(self) -> ConversationSnapshot:
        async with self._state_lock:
            return ConversationSnapshot(list(self.prefix), list(self.living_chunks), self.version)

    async def subscribe(self, subscriber: ConversationSubscriber) -> None:
        async with self._state_lock:
            self._subscribers.add(subscriber)
            await subscriber.on_snapshot(self.id, ConversationSnapshot(
                list(self.prefix),
                list(self.living_chunks),
                self.version,
            ))

    def unsubscribe(self, subscriber: ConversationSubscriber) -> None:
        self._subscribers.discard(subscriber)

    def schedule_delta(self, chunk: StreamEvent) -> None:
        task = asyncio.create_task(self._emit_delta(chunk))
        self._pending_deltas.append(task)
        task.add_done_callback(self._discard_pending_delta)

    def _discard_pending_delta(self, task: asyncio.Task[None]) -> None:
        if task in self._pending_deltas:
            self._pending_deltas.remove(task)

    def queue_task_delivery(self, task_id: str) -> None:
        if task_id not in self._pending_task_deliveries:
            self._pending_task_deliveries.append(task_id)

    def pop_task_deliveries(self) -> list[str]:
        items = self._pending_task_deliveries
        self._pending_task_deliveries = []
        return items

    def pending_task_delivery_ids(self) -> list[str]:
        return list(self._pending_task_deliveries)

    def suppress_task_deliveries(self, now: float, ttl_s: float) -> list[str]:
        self._task_delivery_suppressed_until = now + ttl_s
        return self.pop_task_deliveries()

    def allow_task_deliveries(self) -> None:
        self._task_delivery_suppressed_until = None

    def task_deliveries_suppressed(self, now: float) -> bool:
        if self._task_delivery_suppressed_until is None:
            return False
        if now >= self._task_delivery_suppressed_until:
            self._task_delivery_suppressed_until = None
            return False
        return True

    async def close(self) -> None:
        skip_conversation_task_deliveries(self.runtime, self.id, self.pop_task_deliveries())
        self.stop_event.set()

    def _enter_run(self) -> None:
        if self._running:
            raise ConversationBusy(self.id)
        self._running = True
        self.runtime.conversations.mark_running(self.id)
        self.stop_event.clear()

    async def _exit_run(self) -> None:
        try:
            await self.runtime.drain_pending_task_deliveries(self.id)
        finally:
            self._running = False
            self.runtime.conversations.mark_idle(self.id)

    async def _run_loop(self, on_event: StreamCallback | None) -> list[GenOutput]:
        harness = Harness.from_config(self.harness_config, self.context, self.runtime)
        harness_closed = False

        async def close_harness() -> None:
            nonlocal harness_closed
            if harness_closed:
                return
            try:
                await harness.close()
            except Exception:
                _log.warning("harness cleanup failed for conversation %s", self.id, exc_info=True)
            harness_closed = True

        try:
            while True:
                stop_chunk: StreamEvent | None = None
                async for chunk in self.stream_client.stream(
                    self.history.to_llm_input(),
                    model=self.context.model,
                    context=self.context,
                    cache=self.runtime.cache,
                    stop_event=self.stop_event,
                    metadata=RequestMetadata(
                        trace_id=str(self.context.otel.get("trace_id") or self.id),
                        actor_id=self.context.actor,
                        conversation_id=self.id,
                        purpose="chat",
                    ).to_dict(),
                ):
                    if chunk.kind != "stream_stop":
                        if on_event is not None:
                            await on_event(chunk)
                        await self._emit_delta(chunk)
                    else:
                        stop_chunk = chunk
                merge_chunks = list(self.living_chunks)
                if stop_chunk is not None:
                    merge_chunks.append(stop_chunk)
                outputs, stop = merge(merge_chunks)
                self.last_stop = stop
                continues = stop.reason in {"tool_calls", "function_call"}
                terminal_append: list[dict[str, object]] | None = None
                if continues:
                    await self._emit_commit(outputs, True)
                else:
                    terminal_append = await self._extend(outputs)
                await self._record_usage(stop)
                if stop.reason in {"stop", "interrupted"}:
                    await close_harness()
                    await self._mark_status("interrupted" if stop.reason == "interrupted" else "closed")
                    await self._publish_commit(terminal_append or [], False)
                # The stream_stop frame is sent after history persistence and,
                # for terminal turns, after tool resources have been released
                # and terminal status has been persisted.
                stop_frame = stream_stop_from(stop)
                if on_event is not None:
                    await on_event(stop_frame)

                if stop.reason in {"stop", "interrupted"}:
                    return outputs
                if stop.reason not in {"tool_calls", "function_call"}:
                    await self._mark_status("blocked", reason=stop.reason)
                    raise ConversationBlocked(stop.reason)

                results = await harness.gather(extract_tool_calls(outputs), self.stop_event)
                await self._flush_pending_deltas()
                await self._emit_commit(results, True)
                if self.stop_event.is_set():
                    stop = StreamStop("interrupted")
                    await close_harness()
                    await self._mark_status("interrupted")
                    await self._emit_commit([], False)
                    stop_frame = stream_stop_from(stop)
                    if on_event is not None:
                        await on_event(stop_frame)
                    return outputs
        except Exception as exc:
            await self._emit_error(str(exc))
            raise
        finally:
            await close_harness()

    def _needs_python_reset_notice(self) -> bool:
        for item in reversed(self.history.interaction_items()):
            if isinstance(item, InputMessage) and item.role == "developer" and item.name == "yuubot":
                return False
            if isinstance(item, GenToolCall) and item.name == "execute_python":
                return True
        return False

    async def _extend(self, items: Sequence[HistoryItem]) -> list[dict[str, object]]:
        if not items:
            return []
        self.history.extend(items)
        wrapped_items = await self.runtime.history.extend(self.id, items)
        return [item for item in wrapped_items if str(item["kind"]) not in PREFIX_KINDS]

    async def _emit_delta(self, chunk: StreamEvent) -> None:
        async with self._state_lock:
            self.living_chunks.append(chunk)
            self.version += 1
            for subscriber in list(self._subscribers):
                await subscriber.on_delta(self.id, chunk, self.version)

    async def _emit_commit(self, items: Sequence[HistoryItem], continues: bool) -> None:
        append = await self._extend(items)
        await self._publish_commit(append, continues)

    async def _publish_commit(self, append: list[dict[str, object]], continues: bool) -> None:
        async with self._state_lock:
            self.prefix.extend(append)
            self.living_chunks.clear()
            self.version += 1
            for subscriber in list(self._subscribers):
                await subscriber.on_commit(self.id, append, continues, self.version)

    async def _emit_error(self, error: str) -> None:
        async with self._state_lock:
            for subscriber in list(self._subscribers):
                await subscriber.on_error(self.id, error)

    async def _flush_pending_deltas(self) -> None:
        while self._pending_deltas:
            pending = self._pending_deltas
            self._pending_deltas = []
            await asyncio.gather(*pending)

    async def _record_usage(self, stop: StreamStop) -> None:
        await self.runtime.state.append_usage(self.id, stop.usage, stop.account)
        self.runtime.emit(
            ConversationUsagePayload(
                self.id,
                stop.usage.input_tokens,
                stop.usage.cached_input_tokens,
                stop.usage.cache_write_tokens,
                stop.usage.output_tokens,
                stop.account,
            )
        )

    async def _mark_status(self, status: str, **last_error: object) -> None:
        await self.runtime.state.put_conversation(
            self.id,
            self.context.actor,
            status,
            last_error=dict(last_error) if last_error else None,
        )

class ConversationCreator(Protocol):
    async def spawn_conversation(self, conversation_id: str | None = None) -> Conversation: ...


@define
class ConversationManager:
    """Short-lived index of active Conversation objects with TTL cleanup.

    History is durable in the database; only the runtime objects expire.
    """

    ttl_s: float = 3600
    _items: dict[str, Conversation] = field(factory=dict)
    _idle_since: dict[str, float] = field(factory=dict)
    _create_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    _sweeper: BackgroundSweeper = field(factory=BackgroundSweeper, init=False)

    async def get_or_create(self, creator: ConversationCreator, conversation_id: str | None = None) -> Conversation:
        async with self._create_lock:
            if conversation_id and conversation_id in self._items:
                return self._items[conversation_id]
            conversation = await creator.spawn_conversation(conversation_id)
            self._items[conversation.id] = conversation
            self._idle_since[conversation.id] = time.time()
            return conversation

    async def get_or_load(self, creator: ConversationCreator, conversation_id: str) -> Conversation:
        async with self._create_lock:
            existing = self._items.get(conversation_id)
            if existing is not None:
                return existing
            conversation = await creator.spawn_conversation(conversation_id)
            self._items[conversation.id] = conversation
            self._idle_since[conversation.id] = time.time()
            return conversation

    def mark_running(self, conversation_id: str) -> None:
        if conversation_id in self._items:
            self._idle_since.pop(conversation_id, None)

    def mark_idle(self, conversation_id: str) -> None:
        if conversation_id in self._items:
            self._idle_since[conversation_id] = time.time()

    def interrupt(self, conversation_id: str) -> bool:
        if conversation_id not in self._items:
            return False
        conversation = self._items[conversation_id]
        return conversation.interrupt()

    def interrupt_all(self) -> list[str]:
        interrupted: list[str] = []
        for conversation_id, conversation in list(self._items.items()):
            if conversation.interrupt():
                interrupted.append(conversation_id)
        return interrupted

    def has(self, conversation_id: str) -> bool:
        return conversation_id in self._items

    def running(self, conversation_id: str) -> bool:
        conversation = self._items.get(conversation_id)
        return conversation.running if conversation is not None else False

    def get_if_present(self, conversation_id: str) -> Conversation | None:
        item = self._items.get(conversation_id)
        if item is None:
            return None
        return item

    def schedule_delta(self, conversation_id: str, chunk: StreamEvent) -> bool:
        conversation = self._items.get(conversation_id)
        if conversation is None or not conversation.running:
            return False
        conversation.schedule_delta(chunk)
        return True

    async def discard(self, conversation_id: str) -> bool:
        conversation = self._items.pop(conversation_id, None)
        self._idle_since.pop(conversation_id, None)
        if conversation is None:
            return False
        await conversation.close()
        return True

    async def close_for_actor(self, actor_id: str) -> None:
        for conversation_id in [cid for cid, conv in self._items.items() if conv.context.actor == actor_id]:
            await self.discard(conversation_id)

    async def close_all(self) -> None:
        for conversation_id in list(self._items):
            await self.discard(conversation_id)

    async def sweep(self) -> None:
        now = time.time()
        for conversation_id in [cid for cid, idle_since in self._idle_since.items() if now - idle_since > self.ttl_s]:
            await self.discard(conversation_id)

    async def start_background_cleanup(self, interval_s: float = 60) -> None:
        await self._sweeper.start(interval_s, self.sweep)

    async def stop_background_cleanup(self) -> None:
        await self._sweeper.stop()
