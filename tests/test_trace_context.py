"""Tests for YuubotTraceContextProvider conversation_id injection and propagation."""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

from yuuagents import RuntimeEvent

from yuubot.core.observability import YuubotTraceContextProvider


def test_conversation_id_prefers_injected_over_uuid5() -> None:
    provider = YuubotTraceContextProvider()
    provider.register(
        "test-agent",
        conversation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    event = RuntimeEvent(
        name="agent.started",
        agent_id="agent-1",
        agent_name="test-agent",
        data={},
        timestamp=0.0,
    )

    conv_id = provider.conversation_id(event)
    assert conv_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert conv_id != uuid5(NAMESPACE_DNS, "agent-1")


def test_conversation_id_falls_back_to_uuid5_when_not_injected() -> None:
    provider = YuubotTraceContextProvider()
    provider.register("test-agent")  # no conversation_id

    event = RuntimeEvent(
        name="agent.started",
        agent_id="agent-1",
        agent_name="test-agent",
        data={},
        timestamp=0.0,
    )

    conv_id = provider.conversation_id(event)
    assert conv_id == uuid5(NAMESPACE_DNS, "agent-1")


def test_context_for_propagates_conversation_id() -> None:
    provider = YuubotTraceContextProvider()
    provider.register(
        "test-agent",
        conversation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        character_name="Test Char",
        model="test-model",
    )

    event = RuntimeEvent(
        name="agent.started",
        agent_id="agent-1",
        agent_name="test-agent",
        data={},
        timestamp=0.0,
    )

    ctx = provider._context_for(event)
    assert ctx.conversation_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert ctx.character_name == "Test Char"
    assert ctx.model == "test-model"

    event2 = RuntimeEvent(
        name="llm.started",
        agent_id="agent-1",
        agent_name="test-agent",
        data={},
        timestamp=0.0,
    )
    ctx2 = provider._context_for(event2)
    assert ctx2 is ctx
    assert ctx2.conversation_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_event_attributes_includes_conversation_id() -> None:
    provider = YuubotTraceContextProvider()
    provider.register(
        "test-agent",
        conversation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    event = RuntimeEvent(
        name="agent.started",
        agent_id="agent-1",
        agent_name="test-agent",
        data={},
        timestamp=0.0,
    )

    attrs = provider.event_attributes(event)
    assert attrs["yuubot.conversation_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_conversation_id_accepts_uuid() -> None:
    """register() accepts a UUID directly and stores it as-is."""
    provider = YuubotTraceContextProvider()
    provider.register(
        "uuid-agent",
        conversation_id=UUID("11111111-2222-3333-4444-555555555555"),
    )

    event = RuntimeEvent(
        name="agent.started",
        agent_id="agent-uuid",
        agent_name="uuid-agent",
        data={},
        timestamp=0.0,
    )

    conv_id = provider.conversation_id(event)
    assert conv_id == UUID("11111111-2222-3333-4444-555555555555")


def test_conversation_id_accepts_arbitrary_string() -> None:
    """register() accepts arbitrary string (not UUID format) and stores it."""
    provider = YuubotTraceContextProvider()
    provider.register(
        "string-agent",
        conversation_id="conv_abc_123",
    )

    event = RuntimeEvent(
        name="agent.started",
        agent_id="agent-string",
        agent_name="string-agent",
        data={},
        timestamp=0.0,
    )

    conv_id = provider.conversation_id(event)
    assert conv_id == "conv_abc_123"
