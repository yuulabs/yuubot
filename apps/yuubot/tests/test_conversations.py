"""Unit tests for ConversationManager event handlers and send path."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import yuullm
from yuuagents.core.eventbus import RuntimeEvent

from yuubot.core.conversation_events import ConversationFrontendEvent, ConversationSSEHeartbeat
from yuubot.core.conversations import (
    ConversationBindingConflict,
    ConversationManager,
    ConversationSendBinding,
)


def _mock_store_by_role(role: str) -> tuple[MagicMock, yuullm.Message]:
    store = MagicMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.append_history_item = AsyncMock()
    store.append_history_items = AsyncMock()
    store.get_conversation = AsyncMock(return_value=MagicMock(actor_id="actor-1"))
    store.create_conversation_row = AsyncMock(return_value=MagicMock())
    store.list_history_items = AsyncMock(return_value=[])
    store.history = AsyncMock(return_value=[])

    # The persisted tool-result message recorded by _handle_tool_result.
    persisted_message: list[yuullm.Message] = []

    async def capture_item(
        conversation_id: str,
        item: yuullm.PromptItem,
    ) -> MagicMock:
        if isinstance(item, yuullm.Message):
            persisted_message.append(item)
        record = MagicMock()
        record.message_id = "msg-1"
        record.conversation_id = conversation_id
        return record

    store.append_history_item = AsyncMock(side_effect=capture_item)
    return store, persisted_message


async def test_handle_tool_result_persists_canonical_tool_message() -> None:
    """``_handle_tool_result`` persists a ``yuullm.tool(...)`` Message shape
    and returns the visible SSE delta (missing-text branch)."""
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

    store, persisted = _mock_store_by_role("tool")
    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    result = await manager._handle_tool_result("conv-1", event)

    store.append_history_item.assert_called_once()
    call_args = store.append_history_item.call_args
    assert call_args.args[0] == "conv-1"
    persisted_item = call_args.args[1]
    assert isinstance(persisted_item, yuullm.Message)
    assert persisted_item.role == "tool"
    assert persisted_item.content[0]["type"] == "tool_result"
    assert persisted_item.content[0]["tool_call_id"] == "call_abc"
    assert persisted_item.content[0]["content"] == "echoed: hello"

    assert persisted == [persisted_item]

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


async def test_handle_tool_result_failed_status_persists_canonical_message() -> None:
    """``_handle_tool_result`` with status="failed" still persists the
    canonical tool message (tool_call_id + content) and reports the SSE
    delta with the failure text."""
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

    store, persisted = _mock_store_by_role("tool")
    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    result = await manager._handle_tool_result("conv-1", event)

    store.append_history_item.assert_called_once()
    persisted_item = persisted[0]
    assert persisted_item.role == "tool"
    assert persisted_item.content[0]["type"] == "tool_result"
    assert persisted_item.content[0]["tool_call_id"] == "call_xyz"
    assert persisted_item.content[0]["content"] == "Tool nonexistent.tool is not available"

    assert result is not None
    assert result[0].event_type == "transcript_delta"
    assert result[0].as_dict()["deltas"] == [{
        "type": "tool_result",
        "tool_call_id": "call_xyz",
        "tool_name": "nonexistent.tool",
        "text_delta": "Tool nonexistent.tool is not available",
    }]


async def test_turn_completed_emits_named_event_and_keeps_stream_open() -> None:
    """``agent.turn_completed`` projects to a ``turn_completed`` SSE event
    and does **not** close the subscriber's stream.

    Regression guard for the prior "close stream per turn" design that
    caused every second user message to hang: the stream died after the
    first turn, so the next send had no subscriber and dropped every delta.
    """
    manager = manager_with_store()
    manager._agent_to_conversation["agent-1"] = "conv-1"
    subscription = manager.subscribe_events("conv-1", heartbeat_interval=3600.0)
    first = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)

    await manager._on_runtime_event(
        RuntimeEvent(
            name="agent.turn_completed",
            agent_id="agent-1",
            agent_name="test-agent",
            data={"task_id": "task-1"},
            timestamp=1234567892.0,
        ),
    )

    emitted = await asyncio.wait_for(first, timeout=1)
    assert isinstance(emitted, ConversationFrontendEvent)
    assert emitted.event_type == "turn_completed"
    assert emitted.as_dict()["turn_id"] == "task-1"

    # Stream must still be open: a subsequent delta event arrives on the
    # same subscription. This is the exact path the regression broke.
    second = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)
    await manager._on_runtime_event(
        RuntimeEvent(
            name="output.chunk",
            agent_id="agent-1",
            agent_name="test-agent",
            data={"blocks": [{"type": "text", "text": "second-turn"}]},
            timestamp=1234567893.0,
        ),
    )
    second_emitted = await asyncio.wait_for(second, timeout=1)
    assert isinstance(second_emitted, ConversationFrontendEvent)
    assert second_emitted.event_type == "transcript_delta"


async def test_subscribe_events_heartbeat_keeps_idle_stream_alive() -> None:
    """When no event arrives within ``heartbeat_interval``, subscribe_events
    yields a ``ConversationSSEHeartbeat`` so the daemon can emit a comment
    frame and prevent idle-timeout disconnects from middleboxes."""
    manager = manager_with_store()
    subscription = manager.subscribe_events("conv-1", heartbeat_interval=0.05)
    first = asyncio.ensure_future(subscription.__anext__())
    heartbeat = await asyncio.wait_for(first, timeout=1)
    assert isinstance(heartbeat, ConversationSSEHeartbeat)
    assert heartbeat.conversation_id == "conv-1"


async def test_send_message_returns_before_turn_completes() -> None:
    """send_message persists the user Message via ``append_history_item``
    and returns *before* ``handle_conversation_message`` finishes the turn."""
    started = asyncio.Event()
    release = asyncio.Event()
    record = MagicMock()
    record.message_id = "message-1"

    store = MagicMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.append_history_item = AsyncMock(return_value=record)
    store.append_history_items = AsyncMock(return_value=[record])
    store.get_conversation = AsyncMock(return_value=MagicMock())
    store.history = AsyncMock(return_value=[])

    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    runtime = MagicMock()
    fake_agent = MagicMock(id="agent-1")
    runtime.conversation_agents = {}  # cache miss path
    runtime.ensure_conversation_agent = AsyncMock(return_value=fake_agent)

    async def handle_conversation_message(
        conversation_id: str,
        message: yuullm.Message,
    ) -> None:
        _ = conversation_id, message
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
            text="hello",
            message_id="message-1",
        )

    # send_message returns (conversation_record, message_id)
    assert result[1] == "message-1"
    assert store.append_history_item.call_count == 1
    # The persisted item is the user Message as a yuullm.user(...) struct.
    persisted = store.append_history_item.call_args.args[1]
    assert isinstance(persisted, yuullm.Message)
    assert persisted.role == "user"
    assert yuullm.render_message_text(persisted) == "hello"

    # Subsequent-send path with cache hit must NOT trigger prefix persistence.
    assert store.append_history_items.call_count == 0

    assert len(manager._turn_tasks) == 1
    await asyncio.wait_for(started.wait(), timeout=1)

    release.set()
    await asyncio.wait_for(asyncio.gather(*manager._turn_tasks), timeout=1)


async def test_first_send_persists_prefix_and_user_message() -> None:
    """send_message on a brand-new conversation persists the freshly-built
    prefix via ``append_history_items`` and then the user Message via
    ``append_history_item``."""
    prefix_message = yuullm.system("system snapshot v1")

    store = MagicMock()
    store.conversation_exists = AsyncMock(return_value=False)
    store.append_history_item = AsyncMock()
    store.append_history_items = AsyncMock()
    store.create_conversation_row = AsyncMock(return_value=MagicMock())
    store.history = AsyncMock(return_value=[])

    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    fake_agent = MagicMock(id="agent-9")
    fake_agent.history = [prefix_message]

    runtime = MagicMock()
    runtime.conversation_agents = {}
    runtime.ensure_conversation_agent = AsyncMock(return_value=fake_agent)

    binding = ConversationSendBinding(
        conversation_id="conversation-1",
        actor_id="actor-1",
    )

    with (
        patch.object(manager, "_active_actor", new=AsyncMock()),
        patch.object(manager, "_runtime_for", new=AsyncMock(return_value=runtime)),
    ):
        result = await manager.send_message(
            conversation_id="conversation-1",
            text="first hello",
            binding=binding,
            message_id="message-1",
        )

    assert result[1] == "message-1"

    # Prefix persisted in batch AFTER ensure_conversation_agent built it
    # and BEFORE the user Message is appended.
    assert store.append_history_items.call_count == 1
    persisted_prefix = store.append_history_items.call_args.args[1]
    assert persisted_prefix == [prefix_message]

    assert store.append_history_item.call_count == 1
    user_item = store.append_history_item.call_args.args[1]
    assert user_item.role == "user"
    assert yuullm.render_message_text(user_item) == "first hello"


async def test_subsequent_send_with_conflicting_actor_returns_conflict() -> None:
    """A second send supplying an ``actor_id`` that differs from the
    persisted binding raises :class:`ConversationBindingConflict` and does
    not persist the user Message."""
    existing = MagicMock()
    existing.actor_id = "actor-1"
    existing.conversation_id = "conversation-1"

    store = MagicMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.get_conversation = AsyncMock(return_value=existing)
    store.append_history_item = AsyncMock()
    store.history = AsyncMock(return_value=[])

    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )

    binding = ConversationSendBinding(
        conversation_id="conversation-1",
        actor_id="actor-2",
    )

    raised = False
    try:
        await manager.send_message(
            conversation_id="conversation-1",
            text="conflict send",
            binding=binding,
            message_id="message-1",
        )
    except ConversationBindingConflict as exc:
        raised = True
        assert exc.conversation is existing
    assert raised
    store.append_history_item.assert_not_called()


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
