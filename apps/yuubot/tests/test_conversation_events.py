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


def test_assistant_text_output_projects_assistant_delta() -> None:
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

    assert [item.event_type for item in events] == ["assistant_delta"]
    assert events[0].as_dict()["blocks"] == [{"type": "text", "text": "hello"}]


def test_assistant_tool_call_projects_tool_call_started() -> None:
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

    assert [item.event_type for item in events] == ["tool_call_started"]
    payload = events[0].as_dict()
    assert payload["tool_call_id"] == "call-1"
    assert payload["tool_name"] == "execute_python"
    assert payload["arguments"] == {"code": "print(1)"}


def test_tool_output_chunk_projects_snapshot_not_final_result() -> None:
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

    assert [item.event_type for item in events] == ["tool_output_snapshot"]
    payload = events[0].as_dict()
    assert payload["tool_call_id"] == "call-1"
    assert payload["tool_name"] == "execute_python"
    assert payload["content"] == "10%"
    assert payload["complete"] is False


async def test_final_tool_result_commits_rendered_display_state() -> None:
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

    assert [item.event_type for item in events] == [
        "tool_result_committed",
        "message_committed",
    ]
    committed = events[0].as_dict()
    assert committed["content"] == "20%|##        | 2/10done\n"

    call_kwargs = store.append_message.call_args.kwargs
    assert call_kwargs["role"] == "tool"
    assert call_kwargs["content"][0]["content"] == committed["content"]
    assert "10%|#         | 1/10" not in call_kwargs["content"][0]["content"]


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
    store.append_message = AsyncMock()
    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )
    return manager, store
