from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import cast

import msgspec
import yuullm
from attrs import define, field

from yuuagents.core.eventbus import EventBus
from yuuagents.types.values import EventData

type ContentLike = str | yuullm.ContentItem | yuullm.MessageItem
type SubscriberResult = None | Awaitable[None]
type Subscriber = Callable[[], SubscriberResult]
type BlockFactory = Callable[[ContentLike, int], EntityLogBlock]


class ContentBlock(msgspec.Struct, tag="content"):
    block_id: int
    content: ContentLike


class ProcessBlock(msgspec.Struct, tag="process"):
    block_id: int
    content: str
    stream: str = "output"


class CommandBlock(msgspec.Struct, tag="command"):
    block_id: int
    content: str
    exit_code: int | None = None
    duration_s: float | None = None


type EntityLogBlock = ContentBlock | ProcessBlock | CommandBlock


@define
class EntityLog:
    """Append-only output buffer for a running entity."""

    _items: list[ContentLike] = field(factory=list, init=False, repr=False)
    _subscribers: list[Subscriber] = field(factory=list, init=False, repr=False)

    async def write(self, data: ContentLike) -> int:
        offset = len(self._items)
        self._items.append(data)
        for subscriber in list(self._subscribers):
            result = subscriber()
            if inspect.isawaitable(result):
                await result
        return offset

    def read_items(self, offset: int) -> tuple[list[ContentLike], int]:
        if offset < 0:
            offset = 0
        items = self._items[offset:]
        return list(items), len(self._items)

    def tail(self, max_chars: int = 2000) -> str:
        if max_chars <= 0:
            return ""
        remaining = max_chars
        parts: list[str] = []
        for item in reversed(self._items):
            text = item if isinstance(item, str) else yuullm.render_item_text(item)
            if not text:
                continue
            if len(text) > remaining:
                parts.append(text[-remaining:])
                break
            parts.append(text)
            remaining -= len(text)
            if remaining <= 0:
                break
        if not parts:
            return ""
        return "".join(reversed(parts))

    def subscribe(self, cb: Subscriber) -> Callable[[], None]:
        self._subscribers.append(cb)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return unsubscribe


@define
class PeriodicReporter:
    """Flush EntityLog increments to observers and persistence sinks."""

    log: EntityLog
    eventbus: EventBus
    entity_id: str
    entity_type: str
    block_factory: BlockFactory
    parent_id: str = ""
    tool_call_id: str | None = None
    interval: float = 0.5
    _offset: int = field(default=0, init=False, repr=False)
    _chunk_index: int = field(default=0, init=False, repr=False)
    _next_block_id: int = field(default=0, init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _entity_emitted: bool = field(default=False, init=False, repr=False)
    _ended: bool = field(default=False, init=False, repr=False)
    _flush_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)

    async def start(self, interval: float | None = None) -> None:
        if interval is not None:
            self.interval = interval
        await self._emit_entity()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def flush(self) -> None:
        async with self._flush_lock:
            items, next_offset = self.log.read_items(self._offset)
            if not items:
                self._offset = next_offset
                return
            self._offset = next_offset
            blocks = [self._make_block(item) for item in _coalesce(items)]
            payload = self._chunk_payload(blocks)
            await self.eventbus.emit("output.chunk", payload)
            self._chunk_index += 1

    async def flush_final(self, status: str = "completed") -> None:
        if self._ended:
            return
        self._ended = True
        await self._emit_entity()
        await self.flush()
        await self.eventbus.emit(
            "output.entity_end",
            {**self._entity_meta, "status": status},
        )
        await self.stop()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        try:
            while not self._ended:
                await asyncio.sleep(self.interval)
                await self.flush()
        except asyncio.CancelledError:
            raise

    async def _emit_entity(self) -> None:
        if self._entity_emitted:
            return
        self._entity_emitted = True
        await self.eventbus.emit("output.entity", self._entity_meta)

    def _make_block(self, item: ContentLike) -> EntityLogBlock:
        block = self.block_factory(item, self._next_block_id)
        self._next_block_id += 1
        return block

    @property
    def _entity_meta(self) -> EventData:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "parent_id": self.parent_id,
            "tool_call_id": self.tool_call_id,
        }

    def _chunk_payload(self, blocks: list[EntityLogBlock]) -> EventData:
        return {
            **self._entity_meta,
            "chunk_index": self._chunk_index,
            "blocks": blocks,
        }


class _NoopReporter:
    """Reporter that does nothing — used when no eventbus is configured."""

    async def start(self) -> None: ...

    async def flush(self) -> None: ...

    async def flush_final(self, status: str = "completed") -> None: ...

    async def stop(self) -> None: ...


def content_block(item: ContentLike, block_id: int) -> ContentBlock:
    return ContentBlock(block_id=block_id, content=item)


def process_block(item: ContentLike, block_id: int) -> ProcessBlock:
    content = item if isinstance(item, str) else yuullm.render_item_text(item)
    return ProcessBlock(block_id=block_id, content=content)


def blocks_to_json(blocks: list[EntityLogBlock]) -> str:
    return json.dumps(blocks_to_builtins(blocks), ensure_ascii=False)


def blocks_to_builtins(blocks: list[EntityLogBlock]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], msgspec.to_builtins(blocks))


def _coalesce(items: list[ContentLike]) -> list[ContentLike]:
    coalesced: list[ContentLike] = []
    pending: list[str] = []
    for item in items:
        if isinstance(item, str):
            pending.append(item)
            continue
        if pending:
            coalesced.append("".join(pending))
            pending.clear()
        coalesced.append(item)
    if pending:
        coalesced.append("".join(pending))
    return coalesced
