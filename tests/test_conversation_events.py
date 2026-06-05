from __future__ import annotations

import asyncio
from typing import cast

import pytest
from yuuagents.entitylog import ContentBlock, ProcessBlock
from yuuagents.eventbus import RuntimeEvent

from yuubot.core.actors.manager import ActorManager
from yuubot.core.conversations import ConversationManager, ConversationStore
from yuubot.runtime.daemon.app import _sse_event


def test_sse_event_uses_standard_event_stream_framing() -> None:
    event = _sse_event("text", {"value": "hello"})

    assert event.startswith("event: text\n")
    assert 'data: {"value": "hello"}\n\n' == event.split("event: text\n", 1)[1]


@pytest.mark.asyncio
async def test_conversation_event_stream_maps_agent_text_chunks() -> None:
    manager = _manager()
    manager._agent_to_conversation["agent-1"] = "conv-1"

    event_task = asyncio.create_task(anext(manager.subscribe_events("conv-1")))
    await asyncio.sleep(0)
    await manager._on_runtime_event(
        RuntimeEvent(
            name="output.chunk",
            agent_id="",
            agent_name="",
            data={
                "entity_id": "agent-1",
                "entity_type": "agent",
                "chunk_index": 0,
                "blocks": [
                    ContentBlock(
                        block_id=0,
                        content={"type": "text", "text": "hello"},
                    )
                ],
            },
        )
    )

    event = await asyncio.wait_for(event_task, timeout=1)
    assert event.conversation_id == "conv-1"
    assert event.agent_id == "agent-1"
    assert event.event_type == "text"
    assert event.content["chunk_index"] == 0


@pytest.mark.asyncio
async def test_conversation_event_stream_maps_tool_output_chunks() -> None:
    manager = _manager()
    manager._agent_to_conversation["agent-1"] = "conv-1"

    event_task = asyncio.create_task(anext(manager.subscribe_events("conv-1")))
    await asyncio.sleep(0)
    await manager._on_runtime_event(
        RuntimeEvent(
            name="output.chunk",
            agent_id="",
            agent_name="",
            data={
                "entity_id": "tool-1",
                "entity_type": "execute_python",
                "parent_id": "agent-1",
                "tool_call_id": "call-1",
                "chunk_index": 0,
                "blocks": [ProcessBlock(block_id=0, content="stdout")],
            },
        )
    )

    event = await asyncio.wait_for(event_task, timeout=1)
    assert event.conversation_id == "conv-1"
    assert event.agent_id == "agent-1"
    assert event.event_type == "tool_result"
    assert event.content["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_conversation_event_stream_maps_tool_entity_end() -> None:
    manager = _manager()
    manager._agent_to_conversation["agent-1"] = "conv-1"

    event_task = asyncio.create_task(anext(manager.subscribe_events("conv-1")))
    await asyncio.sleep(0)
    await manager._on_runtime_event(
        RuntimeEvent(
            name="output.entity_end",
            agent_id="",
            agent_name="",
            data={
                "entity_id": "tool-1",
                "entity_type": "execute_python",
                "parent_id": "agent-1",
                "tool_call_id": "call-1",
                "status": "completed",
            },
        )
    )

    event = await asyncio.wait_for(event_task, timeout=1)
    assert event.event_type == "tool_result"
    assert event.content["status"] == "completed"


def _manager() -> ConversationManager:
    return ConversationManager(
        store=cast(ConversationStore, object()),
        actors=cast(ActorManager, object()),
    )
