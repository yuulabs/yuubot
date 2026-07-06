from yuubot.app.snapshots import _runtime_event_view
from yuubot.domain.stream import StreamEvent
from yuubot.runtime.events import EventBus, RuntimeEvent


def test_eventbus_does_not_buffer_noisy_stream_deltas() -> None:
    eventbus = EventBus()

    eventbus.emit(
        "conversation.stream",
        conversation_id="c1",
        event=StreamEvent(group_id="text-0", kind="text_delta", payload={"text": "hello"}),
    )
    queued = eventbus.pull_nowait()

    assert queued.kind == "conversation.stream"
    assert eventbus.events == []


def test_runtime_event_view_summarizes_turn_output() -> None:
    event = RuntimeEvent(
        ts="2026-07-05T00:00:00+00:00",
        kind="conversation.output",
        payload={"conversation_id": "c1", "reason": "stop"},
    )

    view = _runtime_event_view(event)

    assert view is not None
    assert view.title == "Turn finished"
    assert view.detail == "Reason: stop"
    assert view.context == {"conversation_id": "c1", "reason": "stop"}


def test_runtime_event_view_skips_raw_history_payloads() -> None:
    event = RuntimeEvent(
        ts="2026-07-05T00:00:00+00:00",
        kind="conversation.history.append",
        payload={"conversation_id": "c1", "item": {"text": "large payload"}},
    )

    assert _runtime_event_view(event) is None


def test_runtime_event_view_collapses_tool_results() -> None:
    event = RuntimeEvent(
        ts="2026-07-05T00:00:00+00:00",
        kind="conversation.tool_results",
        payload={"conversation_id": "c1", "count": 2, "results": [{"large": "payload"}]},
    )

    view = _runtime_event_view(event)

    assert view is not None
    assert view.title == "Tool results ready"
    assert view.detail == "2 results"
    assert view.context == {"conversation_id": "c1", "count": 2}
