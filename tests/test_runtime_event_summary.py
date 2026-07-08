import asyncio
from typing import cast

from yuubot.app.snapshots import _runtime_event_view
from yuubot.domain.stream import StreamEvent, TextDeltaPayload
from yuubot.runtime.event_payloads import ConversationOutputPayload, ConversationStreamPayload
from yuubot.runtime.events import EventBus, ListenerHub, RuntimeEvent

from support.runtime_events import (
    conversation_history_append,
    conversation_output,
    conversation_tool_results,
)


def test_eventbus_does_not_buffer_noisy_stream_deltas() -> None:
    eventbus = EventBus()

    eventbus.emit(
        ConversationStreamPayload(
            "c1",
            StreamEvent("text-0", "text_delta", TextDeltaPayload("hello")),
        )
    )
    queued = eventbus.pull_nowait()

    assert queued.kind == "conversation.stream"
    assert eventbus.events == []


def test_eventbus_queue_is_bounded_under_noisy_stream_load() -> None:
    eventbus = EventBus()
    eventbus._queue = asyncio.Queue(maxsize=1)

    eventbus.emit(
        ConversationStreamPayload(
            "c1",
            StreamEvent("text-1", "text_delta", TextDeltaPayload("first")),
        )
    )
    eventbus.emit(
        ConversationStreamPayload(
            "c1",
            StreamEvent("text-2", "text_delta", TextDeltaPayload("dropped")),
        )
    )
    eventbus.emit(ConversationOutputPayload("c1", "stop"))

    queued = eventbus.pull_nowait()
    assert queued.kind == "conversation.output"
    assert eventbus.pending_empty()


async def test_listener_hub_dispatches_listeners_concurrently() -> None:
    eventbus = EventBus()
    hub = ListenerHub(eventbus)
    slow_started = asyncio.Event()
    slow_release = asyncio.Event()
    fast_called = asyncio.Event()

    class SlowListener:
        async def on_event(self, event: RuntimeEvent) -> None:
            del event
            slow_started.set()
            await slow_release.wait()

    class FastListener:
        async def on_event(self, event: RuntimeEvent) -> None:
            del event
            fast_called.set()

    hub.add(SlowListener())
    hub.add(FastListener())
    await hub.start()
    eventbus.emit(ConversationOutputPayload("c1", "stop"))
    await asyncio.wait_for(slow_started.wait(), timeout=0.2)
    try:
        await asyncio.wait_for(fast_called.wait(), timeout=0.05)
    finally:
        slow_release.set()
        await hub.stop()


async def test_listener_hub_recovers_after_pull_error() -> None:
    class FlakyEventBus:
        def __init__(self) -> None:
            self.calls = 0
            self.queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

        async def pull(self) -> RuntimeEvent:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary pull failure")
            return await self.queue.get()

        def pull_nowait(self) -> RuntimeEvent:
            return self.queue.get_nowait()

        def pending_empty(self) -> bool:
            return self.queue.empty()

    bus = FlakyEventBus()
    hub = ListenerHub(cast(EventBus, bus))
    called = asyncio.Event()

    class Listener:
        async def on_event(self, event: RuntimeEvent) -> None:
            del event
            called.set()

    hub.add(Listener())
    await hub.start()
    await bus.queue.put(RuntimeEvent("conversation.output", ConversationOutputPayload("c1", "stop"), "now"))
    try:
        await asyncio.wait_for(called.wait(), timeout=0.5)
    finally:
        await hub.stop()


def test_runtime_event_view_summarizes_turn_output() -> None:
    view = _runtime_event_view(conversation_output("c1", "stop"))

    assert view is not None
    assert view.title == "Turn finished"
    assert view.detail == "Reason: stop"
    assert view.context == {"conversation_id": "c1", "reason": "stop"}


def test_runtime_event_view_skips_raw_history_payloads() -> None:
    assert _runtime_event_view(conversation_history_append("c1", {"text": "large payload"})) is None


def test_runtime_event_view_collapses_tool_results() -> None:
    view = _runtime_event_view(conversation_tool_results("c1", 2, [{"large": "payload"}]))

    assert view is not None
    assert view.title == "Tool results ready"
    assert view.detail == "2 results"
    assert view.context == {"conversation_id": "c1", "count": 2}
