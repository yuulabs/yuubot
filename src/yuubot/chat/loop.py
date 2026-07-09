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
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Protocol

import msgspec
from attrs import define, field

from .harness import Harness, HarnessConfig
from .history import PREFIX_KINDS, HistoryHelper
from .titles import title_from_user_message
from ..actor.prompt import SessionMode, augment_user_message
from ..llm import Provider
from ..domain.messages import (
    ContentItem,
    ConversationContext,
    GenOutput,
    GenToolCall,
    HistoryItem,
    InputMessage,
    text_content,
)
from ..domain.stream import StreamEvent, StreamStop, estimate_cost, extract_tool_calls, merge
from ..runtime.event_payloads import (
    ConversationCostPayload,
    ConversationHistoryAppendPayload,
    ConversationInputPayload,
    ConversationOutputPayload,
    ConversationStreamPayload,
    ConversationToolProgressPayload,
    ConversationToolResultsPayload,
    RuntimeEventPayload,
)
from ..runtime.tasks import skip_conversation_task_deliveries
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


@define
class LiveReplayPayload:
    seq: int
    payload: RuntimeEventPayload


@define
class Conversation:
    id: str
    context: ConversationContext
    history: HistoryHelper
    provider: Provider
    harness_config: HarnessConfig
    runtime: "Runtime"
    stop_event: asyncio.Event = field(factory=asyncio.Event)
    _running: bool = field(default=False, init=False)
    _pending_task_deliveries: list[str] = field(factory=list, init=False)
    _task_delivery_suppressed_until: float | None = field(default=None, init=False)
    _live_replay_payloads: list[LiveReplayPayload] = field(factory=list, init=False)
    _live_replay_seq: int = field(default=0, init=False)
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
        try:
            await self._mark_status("active")
            if input.role == "user":
                self.runtime.allow_task_deliveries(self.id)
                if session_mode is not None:
                    input = augment_user_message(input, session_mode)
                await self.runtime.state.set_conversation_title_if_empty(
                    self.id,
                    title_from_user_message(input),
                )
            if self._needs_python_reset_notice():
                await self._append(
                    InputMessage("developer", "yuubot", [ContentItem("text", PYTHON_RESET_NOTICE)])
                )
            await self._append(input)
            self.runtime.emit(
                ConversationInputPayload(self.id, msgspec.to_builtins(input.content))
            )
            return await self._run_loop(on_event)
        except asyncio.CancelledError:
            await self._mark_status("interrupted")
            raise
        finally:
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
            await self._append(InputMessage("developer", name, text_content(text)))
            return await self._run_loop(on_event)
        finally:
            await self._exit_run()

    async def append_items(self, items: Sequence[HistoryItem]) -> None:
        if self._running:
            raise ConversationBusy(self.id)
        await self._extend(items)

    def interrupt(self) -> bool:
        if not self._running:
            return False
        self.runtime.suppress_task_deliveries(self.id)
        self.stop_event.set()
        return True

    def record_live_payload(self, payload: RuntimeEventPayload) -> int:
        if not self._running or not _is_live_replay_payload(payload):
            return 0
        self._live_replay_seq += 1
        self._live_replay_payloads.append(LiveReplayPayload(self._live_replay_seq, payload))
        return self._live_replay_seq

    def live_replay_payloads(self) -> list[LiveReplayPayload]:
        if not self._running:
            return []
        return list(self._live_replay_payloads)

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
        self._live_replay_payloads.clear()
        self._live_replay_seq = 0
        self.runtime.conversations.mark_running(self.id)
        self.stop_event.clear()

    async def _exit_run(self) -> None:
        try:
            await self.runtime.drain_pending_task_deliveries(self.id)
        finally:
            self._running = False
            self._live_replay_payloads.clear()
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
                chunks: list[StreamEvent] = []
                async for chunk in self.provider.stream(
                    self.history.to_llm_input(),
                    model=self.context.model,
                    context=self.context,
                    cache=self.runtime.cache,
                    stop_event=self.stop_event,
                ):
                    chunks.append(chunk)
                    if chunk.kind != "stream_stop":
                        if on_event is not None:
                            await on_event(chunk)
                        self.runtime.emit(
                            ConversationStreamPayload(self.id, chunk)
                        )
                outputs, stop = merge(chunks)
                self.last_stop = stop
                await self._extend(outputs)
                await self._record_cost(stop)
                self.runtime.emit(ConversationOutputPayload(self.id, stop.reason))
                if stop.reason in {"stop", "interrupted"}:
                    await close_harness()
                    await self._mark_status("interrupted" if stop.reason == "interrupted" else "closed")
                # The stream_stop frame is sent after history persistence and,
                # for terminal turns, after tool resources have been released
                # and terminal status has been persisted.
                stop_frame = stream_stop_from(stop)
                if on_event is not None:
                    await on_event(stop_frame)
                self.runtime.emit(
                    ConversationStreamPayload(self.id, stop_frame)
                )

                if stop.reason in {"stop", "interrupted"}:
                    return outputs
                if stop.reason not in {"tool_calls", "function_call"}:
                    await self._mark_status("blocked", reason=stop.reason)
                    raise ConversationBlocked(stop.reason)

                results = await harness.gather(extract_tool_calls(outputs), self.stop_event)
                await self._extend(results)
                self.runtime.emit(
                    ConversationToolResultsPayload(
                        self.id,
                        len(results),
                        msgspec.to_builtins(results),
                    )
                )
                if self.stop_event.is_set():
                    stop = StreamStop("interrupted")
                    self.runtime.emit(ConversationOutputPayload(self.id, stop.reason))
                    await close_harness()
                    await self._mark_status("interrupted")
                    stop_frame = stream_stop_from(stop)
                    if on_event is not None:
                        await on_event(stop_frame)
                    self.runtime.emit(
                        ConversationStreamPayload(self.id, stop_frame)
                    )
                    return outputs
        finally:
            await close_harness()

    def _needs_python_reset_notice(self) -> bool:
        for item in reversed(self.history.interaction_items()):
            if isinstance(item, InputMessage) and item.role == "developer" and item.name == "yuubot":
                return False
            if isinstance(item, GenToolCall) and item.name == "execute_python":
                return True
        return False

    async def _append(self, item: HistoryItem) -> None:
        await self._extend([item])

    async def _extend(self, items: Sequence[HistoryItem]) -> None:
        self.history.extend(items)
        wrapped_items = await self.runtime.history.extend(self.id, items)
        for item in wrapped_items:
            if str(item["kind"]) not in PREFIX_KINDS:
                self.runtime.emit(
                    ConversationHistoryAppendPayload(self.id, item)
                )

    async def _record_cost(self, stop: StreamStop) -> None:
        estimated = stop.cost_estimated or stop.usage.payg_cost is None
        payg_cost = stop.usage.payg_cost if stop.usage.payg_cost is not None else estimate_cost(self.context.model, stop.usage)
        await self.runtime.state.append_cost(self.id, stop.usage, stop.account, estimated)
        self.runtime.emit(
            ConversationCostPayload(
                self.id,
                stop.usage.input_tokens,
                stop.usage.cached_input_tokens,
                stop.usage.output_tokens,
                payg_cost,
                estimated,
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

    def record_live_payload(self, payload: RuntimeEventPayload) -> int:
        conversation_id = _payload_conversation_id(payload)
        if conversation_id is None:
            return 0
        conversation = self._items.get(conversation_id)
        if conversation is not None:
            return conversation.record_live_payload(payload)
        return 0

    def live_replay_payloads(self, conversation_id: str) -> list[LiveReplayPayload]:
        conversation = self._items.get(conversation_id)
        if conversation is None:
            return []
        return conversation.live_replay_payloads()

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


def _is_live_replay_payload(payload: RuntimeEventPayload) -> bool:
    return isinstance(
        payload,
        (
            ConversationStreamPayload,
            ConversationOutputPayload,
            ConversationToolResultsPayload,
            ConversationToolProgressPayload,
        ),
    )


def _payload_conversation_id(payload: RuntimeEventPayload) -> str | None:
    conversation_id = getattr(payload, "conversation_id", None)
    return conversation_id if isinstance(conversation_id, str) and conversation_id else None
