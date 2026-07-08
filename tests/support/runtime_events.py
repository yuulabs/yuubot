from __future__ import annotations

from yuubot.domain.stream import StreamEvent
from yuubot.runtime.event_payloads import (
    ConversationHistoryAppendPayload,
    ConversationOutputPayload,
    ConversationStreamPayload,
    ConversationToolProgressPayload,
    ConversationToolResultsPayload,
    RuntimeEventPayload,
)
from yuubot.runtime.events import RuntimeEvent


def runtime_event(kind: str, payload: RuntimeEventPayload, ts: str = "2026-07-05T00:00:00+00:00") -> RuntimeEvent:
    return RuntimeEvent(kind, payload, ts)


def conversation_stream(conversation_id: str, event: StreamEvent, ts: str = "2026-07-05T00:00:00+00:00") -> RuntimeEvent:
    return runtime_event(
        "conversation.stream",
        ConversationStreamPayload(conversation_id, event),
        ts,
    )


def conversation_output(conversation_id: str, reason: str, ts: str = "2026-07-05T00:00:00+00:00") -> RuntimeEvent:
    return runtime_event(
        "conversation.output",
        ConversationOutputPayload(conversation_id, reason),
        ts,
    )


def conversation_tool_results(
    conversation_id: str,
    count: int,
    results: list[object],
    ts: str = "2026-07-05T00:00:00+00:00",
) -> RuntimeEvent:
    return runtime_event(
        "conversation.tool_results",
        ConversationToolResultsPayload(conversation_id, count, results),
        ts,
    )


def conversation_tool_progress(
    conversation_id: str,
    tool_call_id: str = "call-1",
    tool_name: str = "bash",
    text: str = "",
    task: str = "",
    ts: str = "2026-07-05T00:00:00+00:00",
) -> RuntimeEvent:
    return runtime_event(
        "conversation.tool_progress",
        ConversationToolProgressPayload(
            conversation_id,
            tool_call_id,
            tool_name,
            text,
            task,
        ),
        ts,
    )


def conversation_history_append(
    conversation_id: str,
    item: dict[str, object],
    ts: str = "2026-07-05T00:00:00+00:00",
) -> RuntimeEvent:
    return runtime_event(
        "conversation.history.append",
        ConversationHistoryAppendPayload(conversation_id, item),
        ts,
    )
