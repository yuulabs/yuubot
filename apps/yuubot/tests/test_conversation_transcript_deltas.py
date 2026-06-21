"""Transcript-delta hotfix regression tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from yuuagents.core.eventbus import RuntimeEvent
from yuuagents.types.values import EventData

from yuubot.core.conversation_events import ConversationSSEProjector
from yuubot.core.conversations import ConversationManager


def event(name: str, data: EventData, timestamp: float = 1.0) -> RuntimeEvent:
    return RuntimeEvent(
        name=name,
        agent_id="agent-1",
        agent_name="Test Agent",
        timestamp=timestamp,
        data=data,
    )


def test_assistant_chunks_project_ordered_transcript_deltas() -> None:
    projector = ConversationSSEProjector()

    events = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "blocks": [
                    {"type": "thinking", "thinking": "t1"},
                    {"type": "thinking", "thinking": "t2"},
                    {"type": "text", "text": "t3"},
                ],
                "chunk_index": 1,
            },
        ),
    )

    assert [item.event_type for item in events] == ["transcript_delta"]
    assert events[0].as_dict()["deltas"] == [
        {"type": "thinking", "text_delta": "t1"},
        {"type": "thinking", "text_delta": "t2"},
        {"type": "text", "text_delta": "t3"},
    ]
    assert "blocks" not in events[0].as_dict()
    assert {item.event_type for item in events}.isdisjoint({"thinking", "text"})


def test_tool_call_block_projects_transcript_delta() -> None:
    projector = ConversationSSEProjector()

    events = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "blocks": [{
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "execute_python",
                    "arguments": {"code": "print(1)"},
                }],
                "chunk_index": 1,
            },
        ),
    )

    assert [item.event_type for item in events] == ["transcript_delta"]
    assert events[0].as_dict()["deltas"] == [{
        "type": "tool_call",
        "tool_call_id": "call-1",
        "tool_name": "execute_python",
        "arguments_delta": {"code": "print(1)"},
    }]


def test_tool_output_chunks_project_appendable_transcript_deltas() -> None:
    projector = ConversationSSEProjector()

    first = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "parent_id": "call-1",
                "tool_name": "execute_python",
                "stream": "stdout",
                "chunk_index": 1,
                "blocks": [{"type": "text", "text": "t5"}],
            },
        ),
    )
    second = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "parent_id": "call-1",
                "tool_name": "execute_python",
                "stream": "stdout",
                "chunk_index": 2,
                "blocks": [{"type": "text", "text": "t6"}],
            },
            timestamp=2.0,
        ),
    )

    assert [item.event_type for item in [*first, *second]] == [
        "transcript_delta",
        "transcript_delta",
    ]
    assert first[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "execute_python",
        "stream": "stdout",
        "text_delta": "t5",
    }]
    assert second[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "execute_python",
        "stream": "stdout",
        "text_delta": "t6",
    }]


async def test_final_tool_result_emits_only_missing_visible_delta() -> None:
    manager = manager_with_store()
    _ = manager._sse_projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "parent_id": "call-1",
                "tool_name": "execute_python",
                "stream": "stdout",
                "chunk_index": 1,
                "blocks": [{"type": "text", "text": "t5"}],
            },
        ),
    )

    events = await manager._handle_tool_result(
        "conversation-1",
        event(
            "tool.result_appended",
            {
                "tool_call_id": "call-1",
                "tool_name": "execute_python",
                "result": "t5t6",
                "status": "completed",
            },
            timestamp=2.0,
        ),
    )

    assert [item.event_type for item in events] == ["transcript_delta"]
    assert events[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "execute_python",
        "text_delta": "t6",
    }]


def manager_with_store() -> ConversationManager:
    store = MagicMock()
    store.append_history_item = AsyncMock()
    store.append_history_items = AsyncMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.list_history_items = AsyncMock(return_value=[])
    return ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )
