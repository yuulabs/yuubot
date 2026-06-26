"""Conversation frontend SSE protocol projection tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from yuuagents.core.eventbus import RuntimeEvent
from yuuagents.types.values import EventData

from yuubot.core.conversation_events import (
    ConversationSSEProjector,
    render_tool_output_final_text,
)
from yuubot.core.conversations import ConversationManager


def event(name: str, data: EventData, timestamp: float = 1.0) -> RuntimeEvent:
    return RuntimeEvent(
        name=name,
        agent_id="agent-1",
        agent_name="Test Agent",
        timestamp=timestamp,
        data=data,
    )


def test_assistant_text_output_projects_transcript_delta() -> None:
    projector = ConversationSSEProjector()

    events = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "blocks": [{"type": "text", "text": "hello"}],
                "chunk_index": 1,
            },
        ),
    )

    assert [item.event_type for item in events] == ["transcript_delta"]
    assert events[0].as_dict()["deltas"] == [
        {"type": "text", "text_delta": "hello"}
    ]


def test_assistant_tool_call_projects_transcript_delta() -> None:
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


def test_tool_output_chunk_projects_transcript_delta() -> None:
    projector = ConversationSSEProjector()

    events = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "parent_id": "call-1",
                "tool_name": "execute_python",
                "chunk_index": 2,
                "blocks": [{"type": "text", "text": "10%\r"}],
            },
        ),
    )

    assert [item.event_type for item in events] == ["transcript_delta"]
    assert events[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "execute_python",
        "stream": "combined",
        "text_delta": "10%",
    }]


async def test_final_tool_result_persists_and_emits_visible_delta() -> None:
    manager, store = manager_with_store()
    progress = "10%|#         | 1/10\r20%|##        | 2/10\rdone\n"

    events = await manager._handle_tool_result(
        "conversation-1",
        event(
            "tool.result_appended",
            {
                "tool_call_id": "call-1",
                "tool_name": "execute_python",
                "result": progress,
                "status": "completed",
            },
        ),
    )

    assert [item.event_type for item in events] == ["transcript_delta"]
    assert events[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "execute_python",
        "text_delta": "20%|##        | 2/10done\n",
    }]

    # Persisted canonical yuullm.tool Message — content carries the final
    # rendered text only (no progress-bar prefix).
    assert store.append_history_item.call_count == 1
    persisted_message = store.append_history_item.call_args.args[1]
    assert persisted_message.role == "tool"
    assert persisted_message.content[0]["type"] == "tool_result"
    assert persisted_message.content[0]["tool_call_id"] == "call-1"
    assert persisted_message.content[0]["content"] == "20%|##        | 2/10done\n"
    assert "10%|#         | 1/10" not in persisted_message.content[0]["content"]


def test_terminal_final_text_handles_backspace_and_strips_ansi() -> None:
    rendered = render_tool_output_final_text(
        "\x1b[32mhelxo\b\blo\x1b[0m\n10%\r20%\r"
    )

    assert rendered == "hello\n20%"


async def test_record_event_sequences_are_monotonic() -> None:
    manager, _store = manager_with_store()

    first = await manager._record_event(
        "conversation-1",
        event("output.chunk", {"blocks": [{"type": "text", "text": "hello"}]}),
    )
    second = await manager._record_event(
        "conversation-1",
        event("agent.turn_completed", {}, timestamp=2.0),
    )

    all_events = first + second
    assert [item.as_dict()["sequence"] for item in all_events] == [1, 2]
    assert [item.event_type for item in all_events] == [
        "transcript_delta",
        "turn_completed",
    ]


def test_projector_does_not_emit_raw_runtime_event_names() -> None:
    projector = ConversationSSEProjector()

    events = projector.project_runtime_event(
        "conversation-1",
        event(
            "output.chunk",
            {
                "parent_id": "call-1",
                "blocks": [{"type": "text", "text": "stdout"}],
            },
        ),
    )

    raw_names = {"output.chunk", "output.entity", "output.entity_end", "tool.result_appended"}
    assert events
    assert {item.event_type for item in events}.isdisjoint(raw_names)


def manager_with_store() -> tuple[ConversationManager, MagicMock]:
    store = MagicMock()
    store.append_history_item = AsyncMock()
    store.append_history_items = AsyncMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.list_history_items = AsyncMock(return_value=[])
    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )
    return manager, store
