"""Unit tests for ConversationManager event handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from yuuagents.core.eventbus import RuntimeEvent

from yuubot.core.conversations import ConversationManager


async def test_handle_tool_result_persists_message() -> None:
    """Verify _handle_tool_result persists a role="tool" message and returns SSE event."""
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

    # Verify SSE event
    assert result is not None
    assert result.event_type == "tool_result"
    assert result.content["tool_call_id"] == "call_abc"
    assert result.content["tool_name"] == "echo.echo"
    assert result.content["result"] == "echoed: hello"
    assert result.content["status"] == "completed"
    assert result.conversation_id == "conv-1"


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
    assert result.event_type == "tool_result"
    assert result.content["status"] == "failed"
