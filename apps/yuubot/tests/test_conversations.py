"""Unit tests for ConversationManager event handlers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import yuullm
from yuuagents.core.eventbus import RuntimeEvent

from yuubot.core.conversations import ConversationManager


async def test_handle_tool_result_persists_message() -> None:
    """Verify _handle_tool_result persists a role="tool" message and returns visible delta."""
    event = RuntimeEvent(
        name="tool.result_appended",
        agent_id="agent-1",
        agent_name="test-agent",
        data={
            "tool_call_id": "call_abc",
            "tool_name": "echo.echo",
            "result": "echoed: hello",
            "status": "completed",
            "task_id": "task-1",
        },
        timestamp=1234567890.0,
    )

    mock_store = MagicMock()
    mock_store.append_message = AsyncMock()

    manager = ConversationManager(
        store=mock_store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    result = await manager._handle_tool_result("conv-1", event)

    # Verify persistence
    mock_store.append_message.assert_called_once()
    call_kwargs = mock_store.append_message.call_args.kwargs
    assert call_kwargs["conversation_id"] == "conv-1"
    assert call_kwargs["role"] == "tool"
    assert call_kwargs["content"][0]["type"] == "tool_result"
    assert call_kwargs["content"][0]["tool_call_id"] == "call_abc"
    assert call_kwargs["content"][0]["tool_name"] == "echo.echo"
    assert call_kwargs["content"][0]["content"] == "echoed: hello"
    assert call_kwargs["content"][0]["status"] == "completed"

    assert result is not None
    assert [event.event_type for event in result] == ["transcript_delta"]
    payload = result[0].as_dict()
    assert payload["conversation_id"] == "conv-1"
    assert payload["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call_abc",
        "tool_name": "echo.echo",
        "text_delta": "echoed: hello",
    }]


async def test_handle_tool_result_failed_status() -> None:
    """Verify _handle_tool_result with status="failed" (KeyError branch)."""
    event = RuntimeEvent(
        name="tool.result_appended",
        agent_id="agent-2",
        agent_name="test-agent",
        data={
            "tool_call_id": "call_xyz",
            "tool_name": "nonexistent.tool",
            "result": "Tool nonexistent.tool is not available",
            "status": "failed",
            "task_id": "",
        },
        timestamp=1234567891.0,
    )

    mock_store = MagicMock()
    mock_store.append_message = AsyncMock()

    manager = ConversationManager(
        store=mock_store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    result = await manager._handle_tool_result("conv-2", event)

    mock_store.append_message.assert_called_once()
    call_kwargs = mock_store.append_message.call_args.kwargs
    assert call_kwargs["role"] == "tool"
    assert call_kwargs["content"][0]["status"] == "failed"

    assert result is not None
    assert result[0].event_type == "transcript_delta"
    assert result[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call_xyz",
        "tool_name": "nonexistent.tool",
        "text_delta": "Tool nonexistent.tool is not available",
    }]


async def test_turn_completed_closes_subscription_without_completion_event() -> None:
    manager = manager_with_store()
    subscription = manager.subscribe_events("conv-1")
    first = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)

    result = await manager._record_event(
        "conv-1",
        RuntimeEvent(
            name="agent.turn_completed",
            agent_id="agent-1",
            agent_name="test-agent",
            data={"task_id": "task-1"},
            timestamp=1234567892.0,
        ),
    )

    assert result == []
    try:
        await first
    except StopAsyncIteration:
        pass
    else:
        raise AssertionError("subscription yielded a completion event")


async def test_send_message_returns_before_turn_completes() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    record = MagicMock()
    record.message_id = "message-1"

    mock_store = MagicMock()
    mock_store.history = AsyncMock(return_value=[])
    mock_store.append_message = AsyncMock(return_value=record)

    manager = ConversationManager(
        store=mock_store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )
    runtime = MagicMock()
    runtime.ensure_conversation_agent = AsyncMock(return_value=MagicMock(id="agent-1"))

    async def handle_conversation_message(
        conversation_id: str,
        message: yuullm.Message,
        history: yuullm.History,
    ) -> None:
        _ = conversation_id, message, history
        started.set()
        await release.wait()

    runtime.handle_conversation_message = handle_conversation_message

    with (
        patch.object(
            manager,
            "_require_conversation",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(manager, "_runtime_for", new=AsyncMock(return_value=runtime)),
    ):
        result = await manager.send_message(
            conversation_id="conversation-1",
            content=[{"type": "text", "text": "hello"}],
            message_id="message-1",
        )

    assert result is record
    assert len(manager._turn_tasks) == 1
    await asyncio.wait_for(started.wait(), timeout=1)

    release.set()
    await asyncio.wait_for(asyncio.gather(*manager._turn_tasks), timeout=1)


def manager_with_store() -> ConversationManager:
    mock_store = MagicMock()
    mock_store.append_message = AsyncMock()
    return ConversationManager(
        store=mock_store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )
