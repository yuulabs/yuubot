from __future__ import annotations

from typing import cast

import pytest
import yuutrace
from opentelemetry.util.types import AttributeValue

from yuuagents.obs.entitylog import ProcessBlock
from yuuagents.core.eventbus import EventBus
from yuuagents.obs.observability import (
    ATTR_ENTITY_BLOCKS,
    ATTR_ENTITY_CHUNK_INDEX,
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PARENT_ID,
    ATTR_ENTITY_STATUS,
    ATTR_ENTITY_TOOL_CALL_ID,
    ATTR_ENTITY_TYPE,
    YuuTraceObserver,
)
from yuuagents.types.values import (
    EventData,
    EventPayload,
    EventValue,
)


class FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, AttributeValue] = {}
        self.ended = False

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(self, name: str) -> FakeSpan:
        span = FakeSpan(name)
        self.spans.append(span)
        return span


@pytest.mark.asyncio
async def test_observer_records_output_events_as_immediate_entity_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuuagents.obs import observability

    tracer = FakeTracer()
    monkeypatch.setattr(observability.trace, "get_tracer", lambda name: tracer)

    bus = EventBus()
    bus.subscribe(YuuTraceObserver())

    await bus.emit(
        "output.entity",
        {
            "entity_id": "task-1",
            "entity_type": "bash",
            "parent_id": "agent-1",
            "tool_call_id": "tc-1",
        },
    )
    await bus.emit(
        "output.chunk",
        {
            "entity_id": "task-1",
            "entity_type": "bash",
            "parent_id": "agent-1",
            "tool_call_id": "tc-1",
            "chunk_index": 0,
            "blocks": [ProcessBlock(block_id=0, content="alpha")],
        },
    )
    await bus.emit(
        "output.entity_end",
        {
            "entity_id": "task-1",
            "entity_type": "bash",
            "parent_id": "agent-1",
            "tool_call_id": "tc-1",
            "status": "completed",
        },
    )

    assert [span.name for span in tracer.spans] == [
        "entity",
        "entity.chunk",
        "entity.end",
    ]
    assert all(span.ended for span in tracer.spans)

    entity_attrs = tracer.spans[0].attributes
    assert entity_attrs[ATTR_ENTITY_ID] == "task-1"
    assert entity_attrs[ATTR_ENTITY_TYPE] == "bash"
    assert entity_attrs[ATTR_ENTITY_PARENT_ID] == "agent-1"
    assert entity_attrs[ATTR_ENTITY_TOOL_CALL_ID] == "tc-1"

    chunk_attrs = tracer.spans[1].attributes
    assert chunk_attrs[ATTR_ENTITY_CHUNK_INDEX] == 0
    assert '"content": "alpha"' in _str_attr(chunk_attrs[ATTR_ENTITY_BLOCKS])

    end_attrs = tracer.spans[2].attributes
    assert end_attrs[ATTR_ENTITY_STATUS] == "completed"


def _str_attr(value: AttributeValue) -> str:
    assert isinstance(value, str)
    return value


class FakeEntity:
    def __init__(self) -> None:
        self.flushed: list[list[dict[str, object]]] = []
        self.status: str | None = None

    def flush(self, blocks: list[dict[str, object]]) -> None:
        self.flushed.append(blocks)

    def end(self, status: str = "completed") -> None:
        self.status = status


class FakeConversation:
    def __init__(self) -> None:
        self.entity = FakeEntity()
        self.started: EventData | None = None

    def start_entity(
        self,
        *,
        entity_id: str,
        entity_type: str,
        parent_id: str = "",
        tool_call_id: str | None = None,
    ) -> FakeEntity:
        self.started = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "parent_id": parent_id,
            "tool_call_id": tool_call_id,
        }
        return self.entity


@pytest.mark.asyncio
async def test_observer_uses_conversation_entity_context_for_output_events() -> None:
    observer = YuuTraceObserver()
    conversation = FakeConversation()
    observer._conversations["agent-1"] = cast(
        yuutrace.ConversationContext, conversation
    )

    bus = EventBus()
    bus.subscribe(observer)

    await bus.emit(
        "output.entity",
        {
            "entity_id": "task-1",
            "entity_type": "bash",
            "parent_id": "agent-1",
            "tool_call_id": "tc-1",
        },
    )
    await bus.emit(
        "output.chunk",
        {
            "entity_id": "task-1",
            "blocks": [ProcessBlock(block_id=0, content="alpha")],
        },
    )
    await bus.emit(
        "output.entity_end",
        {
            "entity_id": "task-1",
            "status": "completed",
        },
    )

    assert conversation.started == {
        "entity_id": "task-1",
        "entity_type": "bash",
        "parent_id": "agent-1",
        "tool_call_id": "tc-1",
    }
    assert conversation.entity.flushed == [
        [
            {
                "type": "process",
                "block_id": 0,
                "content": "alpha",
                "stream": "output",
            }
        ]
    ]
    assert conversation.entity.status == "completed"


class FakeTurnContext:
    """Minimal TurnContext double that records add() calls."""

    def __init__(self) -> None:
        self.items: list[EventValue] = []
        self._usage_calls: list[tuple[object, object]] = []

    def add(self, *items: EventValue) -> None:
        self.items.extend(items)

    def usage(self, usage: object, *, cost: object = None) -> None:
        self._usage_calls.append((usage, cost))


class FakeConversationWithTurn(FakeConversation):
    """Fake conversation that returns a real-ish TurnContext from turn()."""

    def __init__(self) -> None:
        super().__init__()
        self.turn_context = FakeTurnContext()

    def turn(self, role: str) -> FakeTurnContext:
        return self.turn_context


@pytest.mark.asyncio
async def test_on_llm_finished_adds_message_content_to_turn() -> None:
    """_on_llm_finished should add message.content items to the active turn."""
    observer = YuuTraceObserver()
    conversation = FakeConversationWithTurn()
    observer._conversations["agent-1"] = cast(
        yuutrace.ConversationContext, conversation
    )
    turn = conversation.turn_context
    observer._turns["agent-1"] = cast(yuutrace.TurnContext, turn)

    bus = EventBus()
    bus.subscribe(observer)

    # Simulate agent.started to register the conversation
    await bus.emit("agent.started", {"agent_id": "agent-1", "agent_name": "test"})

    # Simulate llm.finished with a message carrying content items
    import yuullm

    message = yuullm.assistant(
        yuullm.ThinkingBlock(thinking="let me think").to_message_item(),
        {"type": "text", "text": "Hello!"},
    )

    class FakeUsage:
        provider = "test"
        model = "test-model"
        input_tokens = 10
        output_tokens = 5
        cache_read_tokens = 0
        cache_write_tokens = 0
        total_tokens = 15

    await bus.emit(
        "llm.finished",
        cast(
            EventPayload,
            {
                "agent_id": "agent-1",
                "agent_name": "test",
                "usage": FakeUsage(),
                "cost": None,
                "message": message,
                "text": "Hello!",
                "tool_calls": [],
                "tool_call_count": 0,
            },
        ),
    )

    # The turn should have received the content items from message.content
    assert len(turn.items) > 0
    # Should contain the thinking block and the text item
    assert any(
        getattr(item, "type", None) == "thinking"
        or (isinstance(item, dict) and item.get("type") == "thinking")
        for item in turn.items
    )
    assert any(
        isinstance(item, dict)
        and item.get("type") == "text"
        and item.get("text") == "Hello!"
        for item in turn.items
    )


@pytest.mark.asyncio
async def test_on_llm_finished_without_message_does_not_crash() -> None:
    """_on_llm_finished should handle missing message gracefully."""

    class FakeUsage:
        provider = "test"
        model = "test-model"
        input_tokens = 10
        output_tokens = 5
        cache_read_tokens = 0
        cache_write_tokens = 0
        total_tokens = 15

    observer = YuuTraceObserver()
    conversation = FakeConversationWithTurn()
    observer._conversations["agent-1"] = cast(
        yuutrace.ConversationContext, conversation
    )
    turn = conversation.turn_context
    observer._turns["agent-1"] = cast(yuutrace.TurnContext, turn)

    bus = EventBus()
    bus.subscribe(observer)

    # llm.finished without a message key
    await bus.emit(
        "llm.finished",
        cast(
            EventPayload,
            {
                "agent_id": "agent-1",
                "agent_name": "test",
                "usage": FakeUsage(),
                "cost": None,
                "text": "Hello!",
                "tool_calls": [],
                "tool_call_count": 0,
            },
        ),
    )

    # Turn items should remain empty (no message to add)
    assert len(turn.items) == 0
    # But usage should still be recorded
    assert len(turn._usage_calls) == 1
