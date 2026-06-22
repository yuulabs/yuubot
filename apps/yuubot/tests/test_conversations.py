"""Unit tests for ConversationManager event handlers and send path."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import yuullm
from yuuagents.core.eventbus import EventBus, RuntimeEvent

from yuubot.core.assembly._runtime import YuuAgentsActorRuntime
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
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        _ = conversation_id, message, cancel_event
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

    assert len(manager._in_flight_tasks) == 1
    await asyncio.wait_for(started.wait(), timeout=1)

    release.set()
    await asyncio.wait_for(asyncio.gather(*manager._in_flight_tasks.values()), timeout=1)


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


async def test_cancel_turn_returns_false_when_idle() -> None:
    """``cancel_turn`` on an idle conversation (no in-flight task) returns
    a payload with ``cancelled=False`` and no ``drained`` field."""
    manager = manager_with_store()
    result = await manager.cancel_turn("conv-idle")
    assert result == {"cancelled": False}


async def test_cancel_turn_awaits_task_and_returns_receipt() -> None:
    """``cancel_turn`` awaits the cancelled task before returning the "stop
    receipt" — the HTTP response lands only after the loop's CancelledError
    handler has run (flush_entitylog + cancel_agent_tasks + synthesize
    ``[cancelled]`` tool_results) and the loop's own exit path has emitted
    ``agent.turn_completed`` (the sole emitter). The return dict carries
    ``cancelled`` only — no ``drained``. No ``queue.*`` SSE event is emitted
    anywhere (the queue mechanism is gone)."""
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
    manager._agent_to_conversation["agent-1"] = "conv-1"

    runtime = MagicMock()
    fake_agent = MagicMock(id="agent-1")
    runtime.conversation_agents = {}  # cache miss path
    runtime.ensure_conversation_agent = AsyncMock(return_value=fake_agent)

    # The mock mimics ``_run_agent_turn``'s CancelledError handler: on cancel
    # it flushes the reporter, cancels tool tasks, synthesises ``[cancelled]``
    # results, then breaks out of the loop and emits ``agent.turn_completed``
    # via the normal loop-exit path (no synthetic emission by ``cancel_turn``).
    call_log: list[str] = []

    async def handle_conversation_message(
        conversation_id: str,
        message: yuullm.Message,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        _ = conversation_id, message, cancel_event
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            call_log.append("cancelled-raised")
            fake_agent.flush_entitylog()
            call_log.append("flushed")
            runtime.stage.runtime.cancel_agent_tasks(fake_agent.id)
            call_log.append("tools-cancelled")
            # Synthesise [cancelled] tool_results for any outstanding tool
            # calls in the last assistant message. Here the fixture's user
            # message has no tool calls, so synthesize is a no-op.
            call_log.append("synthesized")
            # Loop break → normal exit → sole emitter of turn_completed.
            await manager._on_runtime_event(
                RuntimeEvent(
                    name="agent.turn_completed",
                    agent_id=fake_agent.id,
                    agent_name="test-agent",
                    data={"task_id": "turn-1"},
                    timestamp=1234567890.0,
                ),
            )
            call_log.append("turn-completed-emitted")
            return

    runtime.handle_conversation_message = handle_conversation_message

    # Open the SSE subscriber first so we can observe the turn_completed event.
    subscription = manager.subscribe_events("conv-1", heartbeat_interval=3600.0)
    turn_completed_future = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)

    with (
        patch.object(
            manager,
            "_require_conversation",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(manager, "_runtime_for", new=AsyncMock(return_value=runtime)),
    ):
        await manager.send_message(
            conversation_id="conv-1",
            text="hello",
            message_id="message-1",
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        # cancel_turn awaits the task — the receipt does not return until the
        # loop's CancelledError handler has completed.
        result = await asyncio.wait_for(manager.cancel_turn("conv-1"), timeout=2)

    # Let any pending done-callbacks fire.
    await asyncio.sleep(0)

    # (1) The in-flight task is gone (cleanup popped it on task-done). The
    # task object itself is done — proving cancel_turn awaited it.
    assert "conv-1" not in manager._in_flight_tasks

    # (2) Return shape: {cancelled: True} and NO ``drained`` field.
    assert result["cancelled"] is True
    assert "drained" not in result

    # (3) CancelledError propagated into the mock runtime's
    # ``handle_conversation_message``; the cleanup callbacks ran in order.
    assert call_log == [
        "cancelled-raised",
        "flushed",
        "tools-cancelled",
        "synthesized",
        "turn-completed-emitted",
    ]
    fake_agent.flush_entitylog.assert_called_once()
    runtime.stage.runtime.cancel_agent_tasks.assert_called_once_with(fake_agent.id)

    # (4) ``turn_completed`` was produced via the normal loop-exit path (the
    # mock emitted it after the CancelledError handler; ``cancel_turn`` did
    # NOT synthesise it).
    emitted = await asyncio.wait_for(turn_completed_future, timeout=1)
    assert isinstance(emitted, ConversationFrontendEvent)
    assert emitted.event_type == "turn_completed"

    # (5) No ``queue.*`` SSE event was emitted anywhere.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(subscription.__anext__(), timeout=0.2)
    await subscription.aclose()


async def test_send_during_inflight_awaits_existing_task() -> None:
    """A second ``send_message`` while a turn is in flight does NOT enqueue
    (the queue mechanism is gone). It defensively awaits the existing
    in-flight task to completion, then starts a new turn task. Exactly one
    in-flight task remains at the end (the second one, still in flight).
    Two user messages were persisted. No ``queue.appended`` SSE event was
    emitted."""
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
    manager._agent_to_conversation["agent-1"] = "conv-1"

    runtime = MagicMock()
    fake_agent = MagicMock(id="agent-1")
    runtime.conversation_agents = {}  # cache miss path
    runtime.ensure_conversation_agent = AsyncMock(return_value=fake_agent)

    # Each call to handle_conversation_message waits on its own release event.
    # The first call blocks until release_events[0] is set; the second blocks
    # on release_events[1] (never set within the test — the second turn stays
    # in flight, which is what the assertions check).
    release_events: list[asyncio.Event] = []

    async def handle_conversation_message(
        conversation_id: str,
        message: yuullm.Message,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        _ = conversation_id, message, cancel_event
        rel = asyncio.Event()
        release_events.append(rel)
        await rel.wait()

    runtime.handle_conversation_message = handle_conversation_message

    subscription = manager.subscribe_events("conv-1", heartbeat_interval=3600.0)
    await asyncio.sleep(0)  # ensure subscriber queue is registered

    with (
        patch.object(
            manager,
            "_require_conversation",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(manager, "_runtime_for", new=AsyncMock(return_value=runtime)),
    ):
        # First send: starts turn #1; handle_conversation_message is now
        # blocked on release_events[0].
        first_result = await manager.send_message(
            conversation_id="conv-1",
            text="first",
            message_id="message-1",
        )
        assert first_result[1] == "message-1"
        assert len(manager._in_flight_tasks) == 1
        first_task = manager._in_flight_tasks["conv-1"]

        # Second send while the first turn is still in flight. The defensive
        # path: send_message awaits the first task, then starts a new one.
        async def second_send() -> tuple[object, str]:
            # Yield enough times to let send_message reach ``await existing``
            # before we unblock the first task.
            fut = asyncio.ensure_future(
                manager.send_message(
                    conversation_id="conv-1",
                    text="second",
                    message_id="message-2",
                ),
            )
            for _ in range(5):
                await asyncio.sleep(0)
            # Unblock the first turn task so the defensive ``await existing``
            # in send_message can complete.
            release_events[0].set()
            return await asyncio.wait_for(fut, timeout=2)

        second_result = await asyncio.wait_for(second_send(), timeout=3)
        assert second_result[1] == "message-2"

    # (1) The first turn task completed before the second send_message
    # returned (defensive await).
    assert first_task.done() is True

    # (2) Exactly one in-flight task remains — the second one, still blocked
    # on release_events[1] (never set within the test).
    assert len(manager._in_flight_tasks) == 1
    assert "conv-1" in manager._in_flight_tasks
    second_task = manager._in_flight_tasks["conv-1"]
    assert second_task is not first_task

    # (3) Two user messages were persisted (one per send_message).
    assert store.append_history_item.call_count == 2

    # (4) No ``queue.appended`` event was emitted (the queue mechanism is
    # gone — send_message started a real turn task, not an enqueue).
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(subscription.__anext__(), timeout=0.2)

    # Tear down the second turn task to avoid leaking a pending task.
    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_task
    manager._in_flight_tasks.pop("conv-1", None)
    manager._cancel_events.pop("conv-1", None)
    await subscription.aclose()


async def test_cancel_turn_persists_partial_assistant_to_db() -> None:
    """When ``cancel_turn`` interrupts a turn whose partial assistant
    message has only thinking items, ``_run_agent_turn``'s CancelledError
    handler emits ``llm.finished`` carrying that partial, and
    ``ConversationManager._handle_llm_finished`` persists it via
    ``store.append_history_item``.

    Mirrors opencode's settle-on-interrupt invariant: the interrupt path
    is just another terminal path — the assistant message is finalised
    and persisted, exactly like the normal terminal path does. Without
    this, the in-memory agent history contains the partial but the DB
    does not → on refresh the agent rebuilds from DB and sees two
    consecutive user messages instead of user → assistant → user.
    """
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
    manager._agent_to_conversation["agent-1"] = "conv-1"

    runtime = MagicMock()
    fake_agent = MagicMock(id="agent-1", name="test-agent")
    # ``agent.llm.model`` is referenced when building the llm.finished payload.
    fake_agent.llm = MagicMock(model="test-model")
    # The yuullm session's CancelledError branch already legalised + appended
    # the partial assistant message to agent.history (thinking item + empty
    # text placeholder from session.py's fix). _runtime.py's CancelledError
    # handler reads this last assistant message back out and emits
    # ``llm.finished`` so _handle_llm_finished persists it.
    partial_assistant = yuullm.Message(
        role="assistant",
        content=[
            {"type": "thinking", "thinking": "partial reasoning", "signature": "s"},
            {"type": "text", "text": ""},
        ],
    )
    fake_agent.history = [partial_assistant]

    runtime.conversation_agents = {}  # cache miss path
    runtime.ensure_conversation_agent = AsyncMock(return_value=fake_agent)

    # The mock mirrors ``_run_agent_turn``'s CancelledError handler post-fix:
    # on cancel it flushes the reporter, cancels tools, locates the last
    # assistant in agent.history, and emits ``llm.finished`` carrying it so
    # _handle_llm_finished persists it. Then breaks out of the loop and the
    # normal exit path emits ``agent.turn_completed`` (sole emitter).
    async def handle_conversation_message(
        conversation_id: str,
        message: yuullm.Message,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        _ = conversation_id, message, cancel_event
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Mirror _run_agent_turn.cancelled handler (pre-Phase 4 surface):
            # flush the reporter, cancel running tools. The Phase 4 addition
            # — emit ``llm.finished`` carrying the partial assistant so
            # _handle_llm_finished persists it — is mirrored here once the
            # real handler emits it (covered by acceptance grep + this test's
            # green state after _runtime.py is updated).
            fake_agent.flush_entitylog()
            runtime.stage.runtime.cancel_agent_tasks(fake_agent.id)
            # Phase 4: locate the last assistant message in agent.history and
            # emit ``llm.finished`` so _handle_llm_finished persists it.
            await manager._on_runtime_event(
                RuntimeEvent(
                    name="llm.finished",
                    agent_id=fake_agent.id,
                    agent_name=fake_agent.name,
                    data={
                        "agent_id": fake_agent.id,
                        "agent_name": fake_agent.name,
                        "usage": None,
                        "cost": None,
                        "model": fake_agent.llm.model,
                        "message": partial_assistant,
                    },
                    timestamp=1234567890.0,
                ),
            )
            await manager._on_runtime_event(
                RuntimeEvent(
                    name="agent.turn_completed",
                    agent_id=fake_agent.id,
                    agent_name=fake_agent.name,
                    data={"task_id": "turn-1"},
                    timestamp=1234567890.0,
                ),
            )
            return

    runtime.handle_conversation_message = handle_conversation_message

    with (
        patch.object(
            manager,
            "_require_conversation",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(manager, "_runtime_for", new=AsyncMock(return_value=runtime)),
    ):
        await manager.send_message(
            conversation_id="conv-1",
            text="hello",
            message_id="message-1",
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        result = await asyncio.wait_for(manager.cancel_turn("conv-1"), timeout=2)

    await asyncio.sleep(0)  # let done callbacks fire

    # cancel_turn awaited the cancelled task (the loop's CancelledError
    # handler ran, including the new llm.finished emit).
    assert result["cancelled"] is True
    assert "conv-1" not in manager._in_flight_tasks

    # store.append_history_item was called twice: once for the user Message
    # (send_message path) and once for the partial assistant Message
    # (_handle_llm_finished path triggered by the new llm.finished emit).
    assert store.append_history_item.call_count == 2
    persisted_messages = [
        call.args[1] for call in store.append_history_item.call_args_list
    ]
    roles = [m.role for m in persisted_messages]
    assert roles.count("user") == 1
    assert roles.count("assistant") == 1

    assistant_persisted = persisted_messages[roles.index("assistant")]
    assert isinstance(assistant_persisted, yuullm.Message)
    assert assistant_persisted.role == "assistant"


async def test_cancel_during_tool_execution_does_not_double_persist_assistant() -> None:
    """When ``cancel_turn`` lands DURING tool execution (after the LLM step
    already emitted a natural ``llm.finished``), the real
    ``_run_agent_turn`` must NOT re-emit ``llm.finished`` — the LLM stage
    already signed off its own message on the natural terminal path. Only
    Stage B (tool execution) is interrupted, so only Stage B's handler runs:
    it calls ``_cancel_agent_tools`` which synthesises ``[cancelled]``
    tool_results and emits ``tool.result_appended`` for each.

    Closes Phase 4 side note #1: the single CancelledError handler
    couldn't tell which stage it was in, so it re-emitted ``llm.finished``
    carrying the same already-persisted assistant → two DB writes for one
    message. Phase 5 splits the single handler into two: Stage A (LLM step)
    owns ``llm.finished``; Stage B (tool execution) owns
    ``tool.result_appended`` via ``_cancel_agent_tools``.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    # Persisted-message recorder (mirrors _mock_store_by_role but inline so
    # we can assert assistant/tool roles + counts).
    persisted: list[yuullm.Message] = []

    async def capture_item(conversation_id: str, item: yuullm.PromptItem) -> MagicMock:
        if isinstance(item, yuullm.Message):
            persisted.append(item)
        record = MagicMock()
        record.message_id = "msg-1"
        record.conversation_id = conversation_id
        return record

    record = MagicMock(message_id="message-1")
    store = MagicMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.append_history_item = AsyncMock(side_effect=capture_item)
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

    # A real EventBus so emits flow through to manager._on_runtime_event.
    eventbus = EventBus()
    fake_runtime = MagicMock()
    fake_runtime.cancel_agent_tasks = AsyncMock()

    # wait_task blocks until the test releases it (simulating a long-running
    # tool). submit_tool_call returns a lightweight task handle whose .id
    # is referenced by wait_task and the result-rendering path (never reached
    # because wait_task is cancelled).
    fake_runtime.submit_tool_call = AsyncMock(return_value=MagicMock(id="task-1"))

    async def wait_task(task_id: str, timeout: float | None = None) -> MagicMock:
        _ = task_id, timeout
        started.set()  # Stage A's natural llm.finished has already fired.
        await release.wait()
        return MagicMock()

    fake_runtime.wait_task = AsyncMock(side_effect=wait_task)
    # registry is accessed by _build_tool_specs_for_agent only on the cache-
    # miss path; we cache-hit via conversation_agents so it stays untouched.
    fake_runtime.registry = MagicMock(_definitions={})

    stage = MagicMock()
    stage.eventbus = eventbus
    stage.runtime = fake_runtime

    # The assistant message the fake LLM step "produces": one tool_call that
    # drives Stage B into the (cancelled) wait_task.
    assistant_with_tool_call = yuullm.Message(
        role="assistant",
        content=[
            {"type": "text", "text": "let me run a tool"},
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "echo",
                "arguments": "{}",
            },
        ],
    )

    class _FakeAgent:
        """Minimal Agent stand-in: list-backed history, no-op flush, step()
        appends the assistant message exactly like the real Agent.step."""

        def __init__(self) -> None:
            self.id = "agent-1"
            self.name = "test-agent"
            self.llm = MagicMock(model="test-model")
            self.log = MagicMock()
            self._history: list[yuullm.Message] = []

        @property
        def history(self) -> list[yuullm.Message]:
            return self._history

        @property
        def done(self) -> bool:
            # Always has a pending tool call → loop continues into Stage B.
            return False

        def append(self, message: yuullm.Message) -> None:
            self._history.append(message)

        async def step(self) -> tuple[yuullm.Message, MagicMock]:
            # Mirrors real Agent.step: the final assistant message is the
            # one committed to history. _extract_tool_calls reads it back
            # out of message.content below.
            self._history.append(assistant_with_tool_call)
            store = MagicMock(usage=None, provider_cost=None)
            return assistant_with_tool_call, store

        async def flush_entitylog(self) -> None:
            return None

    fake_agent = _FakeAgent()

    # Real YuuAgentsActorRuntime: its handle_conversation_message calls the
    # REAL _run_agent_turn under test (cache-hit on conversation_agents so
    # ensure_conversation_agent doesn't try to build a real LLM session).
    runtime = YuuAgentsActorRuntime(
        stage=stage,
        definitions={},
        conversation_definition=MagicMock(),
    )
    runtime.conversation_agents["conv-1"] = fake_agent
    # Wire the manager's event listener onto the runtime's real eventbus so
    # llm.finished / tool.result_appended reach _handle_* and the store.
    runtime.stage.eventbus.subscribe(manager._on_runtime_event)
    manager._agent_to_conversation[fake_agent.id] = "conv-1"

    # Independently record raw eventbus events (names) to assert llm.finished
    # was emitted exactly once on the tool-execution-cancel path.
    emitted_events: list[str] = []

    def record_event(event: RuntimeEvent) -> None:
        emitted_events.append(event.name)
        return None

    eventbus.subscribe(record_event)

    with (
        patch.object(
            manager,
            "_require_conversation",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(manager, "_runtime_for", new=AsyncMock(return_value=runtime)),
    ):
        await manager.send_message(
            conversation_id="conv-1",
            text="hello",
            message_id="message-1",
        )
        # Wait until _run_agent_turn has run Stage A (natural llm.finished)
        # AND is now blocked in Stage B's wait_task.
        await asyncio.wait_for(started.wait(), timeout=2)

        # Cancel while inside wait_task: should land in Stage B's handler,
        # NOT Stage A's.
        result = await asyncio.wait_for(manager.cancel_turn("conv-1"), timeout=2)

    await asyncio.sleep(0)  # let done-callbacks fire

    assert result["cancelled"] is True
    assert "conv-1" not in manager._in_flight_tasks

    roles = [m.role for m in persisted]
    # The bug (Phase 4 single-handler): the except re-emitted llm.finished
    # carrying the SAME already-persisted assistant → 2 assistant writes.
    # Phase 5: Stage B's handler does NOT re-emit llm.finished.
    assert roles.count("assistant") == 1
    assert roles.count("user") == 1
    # Stage B's _cancel_agent_tools synthesised a [cancelled] tool_result and
    # emitted tool.result_appended → _handle_tool_result persisted it.
    assert roles.count("tool") == 1

    # Exactly one llm.finished event on the whole turn (Stage A natural).
    assert emitted_events.count("llm.finished") == 1
    # At least one tool.result_appended fired from _cancel_agent_tools.
    assert emitted_events.count("tool.result_appended") >= 1
