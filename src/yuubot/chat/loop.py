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
class Conversation:
    id: str
    context: ConversationContext
    history: HistoryHelper
    provider: Provider
    harness_config: HarnessConfig
    runtime: "Runtime"
    stop_event: asyncio.Event = field(factory=asyncio.Event)
    _running: bool = field(default=False, init=False)

    @property
    def running(self) -> bool:
        return self._running

    async def run_loop(
        self,
        input: InputMessage,
        on_event: StreamCallback | None = None,
        *,
        session_mode: SessionMode | None = None,
    ) -> list[GenOutput]:
        if self._running:
            raise ConversationBusy(self.id)
        self._running = True
        self.stop_event.clear()
        try:
            await self._mark_status("active")
            if input.role == "user":
                if session_mode is not None:
                    input = augment_user_message(input, mode=session_mode)
                await self.runtime.state.set_conversation_title_if_empty(
                    self.id,
                    title_from_user_message(input),
                )
            if self._needs_python_reset_notice():
                await self._append(
                    InputMessage(role="developer", name="yuubot", content=[ContentItem(kind="text", text=PYTHON_RESET_NOTICE)])
                )
            await self._append(input)
            self.runtime.emit("conversation.input", conversation_id=self.id, content=msgspec.to_builtins(input.content))
            return await self._run_loop(on_event)
        except asyncio.CancelledError:
            await self._mark_status("interrupted")
            raise
        finally:
            self._running = False
            await self.runtime.drain_pending_task_deliveries(self.id)

    async def run_continuation(self, on_event: StreamCallback | None = None) -> list[GenOutput]:
        if self._running:
            raise ConversationBusy(self.id)
        self._running = True
        self.stop_event.clear()
        try:
            await self._mark_status("active")
            return await self._run_loop(on_event)
        finally:
            self._running = False
            await self.runtime.drain_pending_task_deliveries(self.id)

    async def append_developer_notice(
        self,
        text: str,
        *,
        name: str = "yuubot",
        on_event: StreamCallback | None = None,
    ) -> list[GenOutput]:
        if self._running:
            raise ConversationBusy(self.id)
        self._running = True
        self.stop_event.clear()
        try:
            await self._mark_status("active")
            await self._append(InputMessage(role="developer", name=name, content=text_content(text)))
            return await self._run_loop(on_event)
        finally:
            self._running = False
            await self.runtime.drain_pending_task_deliveries(self.id)

    def interrupt(self) -> bool:
        if not self._running:
            return False
        self.stop_event.set()
        return True

    async def close(self) -> None:
        self.stop_event.set()

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
                            "conversation.stream",
                            conversation_id=self.id,
                            event=chunk,
                        )
                outputs, stop = merge(chunks)
                await self._extend(outputs)
                await self._record_cost(stop)
                self.runtime.emit("conversation.output", conversation_id=self.id, reason=stop.reason)
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
                    "conversation.stream",
                    conversation_id=self.id,
                    event=stop_frame,
                )

                if stop.reason in {"stop", "interrupted"}:
                    return outputs
                if stop.reason not in {"tool_calls", "function_call"}:
                    await self._mark_status("blocked", reason=stop.reason)
                    raise ConversationBlocked(stop.reason)

                results = await harness.gather(extract_tool_calls(outputs), self.stop_event)
                await self._extend(results)
                self.runtime.emit(
                    "conversation.tool_results",
                    conversation_id=self.id,
                    count=len(results),
                    results=msgspec.to_builtins(results),
                )
                if self.stop_event.is_set():
                    stop = StreamStop(reason="interrupted")
                    self.runtime.emit("conversation.output", conversation_id=self.id, reason=stop.reason)
                    await close_harness()
                    await self._mark_status("interrupted")
                    stop_frame = stream_stop_from(stop)
                    if on_event is not None:
                        await on_event(stop_frame)
                    self.runtime.emit(
                        "conversation.stream",
                        conversation_id=self.id,
                        event=msgspec.to_builtins(stop_frame),
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
        self.runtime.conversations.touch(self.id)
        for item in wrapped_items:
            if str(item["kind"]) not in PREFIX_KINDS:
                self.runtime.emit("conversation.history.append", conversation_id=self.id, item=item)

    async def _record_cost(self, stop: StreamStop) -> None:
        estimated = stop.cost_estimated or stop.usage.payg_cost is None
        payg_cost = stop.usage.payg_cost if stop.usage.payg_cost is not None else estimate_cost(self.context.model, stop.usage)
        await self.runtime.state.append_cost(self.id, stop.usage, stop.account, estimated=estimated)
        self.runtime.emit(
            "conversation.cost",
            conversation_id=self.id,
            input_tokens=stop.usage.input_tokens,
            cached_input_tokens=stop.usage.cached_input_tokens,
            output_tokens=stop.usage.output_tokens,
            payg_cost=payg_cost,
            estimated=estimated,
            account=stop.account,
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
    _items: dict[str, tuple[Conversation, float]] = field(factory=dict)
    _create_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    _sweeper: BackgroundSweeper = field(factory=BackgroundSweeper, init=False)

    async def get_or_create(self, creator: ConversationCreator, conversation_id: str | None = None) -> Conversation:
        async with self._create_lock:
            if conversation_id and conversation_id in self._items:
                conversation, _ = self._items[conversation_id]
                self._items[conversation_id] = (conversation, time.time())
                return conversation
            conversation = await creator.spawn_conversation(conversation_id)
            self._items[conversation.id] = (conversation, time.time())
            return conversation

    def touch(self, conversation_id: str) -> None:
        if conversation_id in self._items:
            conversation, _ = self._items[conversation_id]
            self._items[conversation_id] = (conversation, time.time())

    def interrupt(self, conversation_id: str) -> bool:
        if conversation_id not in self._items:
            return False
        conversation, _ = self._items[conversation_id]
        self.touch(conversation_id)
        return conversation.interrupt()

    def interrupt_all(self) -> list[str]:
        interrupted: list[str] = []
        for conversation_id, (conversation, _) in list(self._items.items()):
            if conversation.interrupt():
                self.touch(conversation_id)
                interrupted.append(conversation_id)
        return interrupted

    def has(self, conversation_id: str) -> bool:
        return conversation_id in self._items

    def get_if_present(self, conversation_id: str) -> Conversation | None:
        item = self._items.get(conversation_id)
        if item is None:
            return None
        conversation, _ = item
        self._items[conversation_id] = (conversation, time.time())
        return conversation

    async def discard(self, conversation_id: str) -> bool:
        item = self._items.pop(conversation_id, None)
        if item is None:
            return False
        conversation, _ = item
        await conversation.close()
        return True

    async def close_for_actor(self, actor_id: str) -> None:
        for conversation_id in [cid for cid, (conv, _) in self._items.items() if conv.context.actor == actor_id]:
            await self.discard(conversation_id)

    async def close_all(self) -> None:
        for conversation_id in list(self._items):
            await self.discard(conversation_id)

    async def sweep(self) -> None:
        now = time.time()
        for conversation_id in [cid for cid, (_, seen) in self._items.items() if now - seen > self.ttl_s]:
            await self.discard(conversation_id)

    async def start_background_cleanup(self, interval_s: float = 60) -> None:
        await self._sweeper.start(interval_s, self.sweep)

    async def stop_background_cleanup(self) -> None:
        await self._sweeper.stop()
