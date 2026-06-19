"""Tests for Agent step() lifecycle."""

from __future__ import annotations

import pytest
import yuullm

import yuuagents as ya
from yuuagents.core.eventbus import EventBus
from yuuagents.obs.entitylog import ContentBlock
from yuuagents.types.values import EventData

from .conftest import (
    FakeSessionFactory,
    _make_agent,
    text_response,
    tool_call,
)


# ---------------------------------------------------------------------------
# Agent step() lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_text_only_response_sets_done() -> None:
    bus = EventBus()
    llm = FakeSessionFactory([[text_response("Hello!")]])

    agent = _make_agent(llm, bus)
    agent.append(yuullm.user("Hi"))

    assert not agent.done
    message, store = await agent.step()
    assert agent.done


@pytest.mark.asyncio
async def test_agent_llm_stream_writes_output_chunks() -> None:
    bus = EventBus()
    chunks: list[EventData] = []
    bus.subscribe(
        lambda event: (
            chunks.append(dict(event.data)) if event.name == "output.chunk" else None
        )
    )
    llm = FakeSessionFactory(
        [
            [
                yuullm.ThinkingBlock(thinking="plan"),
                text_response("Hello"),
                tool_call("echo", {"msg": "test"}, call_id="tc_1"),
            ]
        ]
    )

    agent = _make_agent(llm, bus)
    agent.append(yuullm.user("Hi"))
    await agent.step()

    agent_chunks = [chunk for chunk in chunks if chunk["entity_id"] == agent.id]
    assert agent_chunks
    blocks = agent_chunks[0]["blocks"]
    assert isinstance(blocks, list)
    assert all(isinstance(block, ContentBlock) for block in blocks)
    content_blocks = [block for block in blocks if isinstance(block, ContentBlock)]
    contents = [block.content for block in content_blocks]
    assert {"type": "thinking", "thinking": "plan"} in contents
    assert {"type": "text", "text": "Hello"} in contents
    assert {
        "type": "tool_call",
        "id": "tc_1",
        "name": "echo",
        "arguments": '{"msg": "test"}',
    } in contents


@pytest.mark.asyncio
async def test_agent_streams_reasoning_before_final_text_without_duplicate_thinking() -> (
    None
):
    bus = EventBus()
    events: list[ya.RuntimeEvent] = []
    chunks: list[EventData] = []
    bus.subscribe(lambda event: events.append(event))
    bus.subscribe(
        lambda event: (
            chunks.append(dict(event.data)) if event.name == "output.chunk" else None
        )
    )
    llm = FakeSessionFactory(
        [
            [
                yuullm.Reasoning({"type": "text", "text": "plan"}),
                text_response("Hello"),
                yuullm.ThinkingBlock(thinking="plan"),
            ]
        ]
    )

    agent = _make_agent(llm, bus)
    agent.append(yuullm.user("Hi"))
    message, store = await agent.step()

    agent_chunks = [chunk for chunk in chunks if chunk["entity_id"] == agent.id]
    assert agent_chunks
    blocks = agent_chunks[0]["blocks"]
    assert isinstance(blocks, list)
    contents = [block.content for block in blocks if isinstance(block, ContentBlock)]
    assert contents == [
        {"type": "thinking", "thinking": "plan"},
        {"type": "text", "text": "Hello"},
    ]
    visible_text = "".join(
        yuullm.render_item_text(item)
        for item in message.content
        if yuullm.is_text_item(item)
    )
    assert visible_text == "Hello"


@pytest.mark.asyncio
async def test_agent_close_ends_entity() -> None:
    bus = EventBus()
    events: list[ya.RuntimeEvent] = []
    bus.subscribe(lambda event: events.append(event))

    agent = _make_agent(FakeSessionFactory([]), bus)
    await agent.close()
    await agent.close()

    end_events = [event for event in events if event.name == "output.entity_end"]
    assert len(end_events) == 1
    assert end_events[0].data["entity_id"] == agent.id
    assert end_events[0].data["status"] == "closed"


@pytest.mark.asyncio
async def test_agent_append_message_resets_done() -> None:
    bus = EventBus()
    llm = FakeSessionFactory(
        [
            [text_response("First response")],
            [text_response("Second response")],
        ]
    )
    agent = _make_agent(llm, bus)
    agent.append(yuullm.user("Message 1"))
    await agent.step()
    assert agent.done

    agent.append(yuullm.user("Message 2"))
    assert not agent.done
    await agent.step()
    assert agent.done


@pytest.mark.asyncio
async def test_llm_cost_charges_usd_budget_unit() -> None:
    bus = EventBus()
    llm = FakeSessionFactory([[text_response("expensive")]], cost_total=0.25)
    agent = _make_agent(llm, bus)
    agent.append(yuullm.user("Hi"))

    message, store = await agent.step()

    assert store.cost is not None
    assert store.cost.total_cost == 0.25


@pytest.mark.asyncio
async def test_actor_loop_stops_when_budget_is_exceeded() -> None:
    bus = EventBus()
    llm = FakeSessionFactory(
        [
            [text_response("first")],
        ]
    )
    agent = _make_agent(llm, bus)
    agent.append(yuullm.user("Hi"))

    actor = ya.ExampleActor(
        ya.Stage(
            mailbox=ya.MailBox(),
            eventbus=bus,
            runtime=ya.Runtime(registry=ya.ToolRegistry(), eventbus=bus),
            llm_session_factories={"fake": llm},
        )
    )
    await actor.run_agent_loop(agent)

    messages, _tools = yuullm.split_history(agent.history)
    assert yuullm.render_message_text(messages[-1]) == "first"
