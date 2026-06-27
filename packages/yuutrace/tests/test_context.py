from __future__ import annotations

import json
import warnings
import uuid

import msgspec
import pytest
import yuullm
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import yuutrace as ytrace


def _reset_tracer_provider(provider: TracerProvider | None = None) -> None:
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    if provider is not None:
        trace.set_tracer_provider(provider)


@pytest.fixture(autouse=True)
def _fresh_tracer_provider():
    """Give each test its own TracerProvider so they don't interfere."""
    provider = TracerProvider()
    _reset_tracer_provider(provider)
    ytrace.init()
    yield
    provider.shutdown()
    _reset_tracer_provider()


def _make_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def _get_turn_spans(exporter: InMemorySpanExporter) -> list[dict]:
    """Extract turn child spans, sorted by start time."""
    spans = exporter.get_finished_spans()
    turn_spans = [s for s in spans if s.name == "turn"]
    turn_spans.sort(key=lambda s: s.start_time)
    return [
        {
            "role": s.attributes.get("yuu.turn.role"),
            "items": json.loads(s.attributes.get("yuu.turn.items", "[]")),
            "start_time": s.attributes.get("yuu.turn.start_time"),
            "_span": s,
        }
        for s in turn_spans
    ]


# ---------------------------------------------------------------------------
# Initialization/no-op tests
# ---------------------------------------------------------------------------


def test_unconfigured_tracing_is_noop_and_warns_once() -> None:
    _reset_tracer_provider()

    with pytest.warns(RuntimeWarning, match="yuutrace is not initialized") as caught:
        with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
            chat.system("You are helpful.", tools=[{"name": "search"}])
            chat.user("hello")
            with chat.tool_batch() as tools:
                with tools.tool(name="search", call_id="tc_1", input={"q": "x"}) as ts:
                    ts.ok("ok")
        ytrace.record_llm_usage(provider="openai", model="gpt-4o", input_tokens=1)
        ytrace.record_cost(category="llm", currency="USD", amount=0.01)

    assert len(caught) == 1


def test_unconfigured_recording_skips_validation_and_serialization() -> None:
    _reset_tracer_provider()

    with pytest.warns(RuntimeWarning, match="yuutrace is not initialized") as caught:
        ytrace.record_llm_usage()
        ytrace.record_cost(category="not-a-category", currency="USD", amount=0.01)

        with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
            chat.system("You are helpful.", tools=object())
            with chat.tool_batch() as tools:
                with tools.tool(name="search", call_id="tc_1", input={}) as ts:
                    ts.ok(object())

    assert len(caught) == 1


def test_trace_span_records_short_lived_span() -> None:
    exporter = _make_exporter()

    with ytrace.trace_span(
        "conversation.send",
        {
            "yuubot.stage": "runtime_ready",
            "yuubot.unsupported": object(),
        },
    ) as span:
        span.attrs(
            **{
                "yuubot.conversation_id": "conversation-1",
                "yuubot.history_count": 2,
                "yuubot.ignored": object(),
            }
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    recorded = spans[0]
    assert recorded.name == "conversation.send"
    assert recorded.attributes["yuubot.stage"] == "runtime_ready"
    assert recorded.attributes["yuubot.conversation_id"] == "conversation-1"
    assert recorded.attributes["yuubot.history_count"] == 2
    assert "yuubot.unsupported" not in recorded.attributes
    assert "yuubot.ignored" not in recorded.attributes
    assert recorded.start_time <= recorded.end_time


def test_explicit_disable_is_noop_without_warning() -> None:
    exporter = _make_exporter()
    ytrace.disable()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
            chat.user("hello")
            with chat.tool_batch() as tools:
                with tools.tool(name="search", call_id="tc_1", input={}) as ts:
                    ts.ok("ok")
        ytrace.record_llm_usage(provider="openai", model="gpt-4o", input_tokens=1)

    assert not [w for w in caught if "yuutrace is not initialized" in str(w.message)]
    assert exporter.get_finished_spans() == ()


def test_disabled_recording_skips_validation_and_serialization() -> None:
    exporter = _make_exporter()
    ytrace.disable()

    ytrace.record_llm_usage()
    ytrace.record_cost(category="not-a-category", currency="USD", amount=0.01)

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.system("You are helpful.", tools=object())
        with chat.tool_batch() as tools:
            with tools.tool(name="search", call_id="tc_1", input={}) as ts:
                ts.ok(object())

    assert exporter.get_finished_spans() == ()


def test_init_reenables_disabled_tracing_and_reuses_provider() -> None:
    exporter = _make_exporter()
    ytrace.disable()

    ytrace.init(service_name="ignored")

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user("hello")

    spans = exporter.get_finished_spans()
    # conversation span exports immediately at creation; turn exports after
    assert [s.name for s in spans] == ["conversation", "turn"]


# ---------------------------------------------------------------------------
# Turn API tests
# ---------------------------------------------------------------------------


def test_user_turn_records_items_as_event() -> None:
    """chat.user(*items) records a yuu.turn event with role=user."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user({"type": "text", "text": "hello"})

    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["role"] == "user"
    assert turns[0]["items"] == [{"type": "text", "text": "hello"}]


def test_user_turn_accepts_str() -> None:
    """chat.user("text") auto-wraps str to TextItem."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user("hello world")

    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["items"] == [{"type": "text", "text": "hello world"}]


def test_user_turn_multiple_items() -> None:
    """chat.user() accepts multiple items including mixed types."""
    exporter = _make_exporter()

    items = [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
    ]
    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user(*items)

    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["items"] == items


def test_user_turn_preserves_image_items() -> None:
    """Image items in user turn are serialized correctly."""
    exporter = _make_exporter()

    img_item = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,iVBOR..."},
    }
    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user({"type": "text", "text": "see image"}, img_item)

    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["items"][1] == img_item


def test_assistant_turn_manual_lifecycle() -> None:
    """start_turn('assistant') records items and can end manually."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        turn = chat.start_turn("assistant")
        turn.add({"type": "text", "text": "I can help"})
        turn.end()

    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["role"] == "assistant"
    assert turns[0]["items"] == [{"type": "text", "text": "I can help"}]
    assert turns[0]["start_time"] is not None


def test_turn_context_manager() -> None:
    """with chat.turn('user') as t: ... emits turn event on __exit__."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        with chat.turn("user") as t:
            t.add({"type": "text", "text": "hi"})

    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["role"] == "user"


def test_entity_context_records_immediate_entity_spans() -> None:
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        entity = chat.start_entity(
            entity_id="task-1",
            entity_type="bash",
            parent_id="agent-1",
            tool_call_id="tc-1",
        )
        entity.flush(
            [
                {
                    "type": "process",
                    "block_id": 0,
                    "content": "alpha",
                    "stream": "output",
                }
            ]
        )
        entity.end("completed")

    spans = exporter.get_finished_spans()
    entity_spans = [span for span in spans if span.name.startswith("entity")]
    assert [span.name for span in entity_spans] == [
        "entity",
        "entity.chunk",
        "entity.end",
    ]
    assert entity_spans[0].attributes["yuu.entity.id"] == "task-1"
    assert entity_spans[0].attributes["yuu.entity.type"] == "bash"
    assert entity_spans[0].attributes["yuu.entity.parent_id"] == "agent-1"
    assert entity_spans[0].attributes["yuu.entity.tool_call_id"] == "tc-1"
    assert entity_spans[1].attributes["yuu.entity.chunk.index"] == 0
    blocks = json.loads(entity_spans[1].attributes["yuu.entity.blocks"])
    assert blocks[0]["content"] == "alpha"
    assert entity_spans[2].attributes["yuu.entity.status"] == "completed"


def test_assistant_turn_with_usage() -> None:
    """TurnContext.usage() records usage events on the conversation span."""
    exporter = _make_exporter()

    class FakeUsage:
        provider = "openai"
        model = "gpt-4"
        input_tokens = 10
        output_tokens = 20
        cache_read_tokens = 0
        cache_write_tokens = 0
        total_tokens = 30

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        turn = chat.start_turn("assistant")
        turn.add({"type": "text", "text": "response"})
        turn.usage(FakeUsage())
        turn.end()

    # Verify turn event
    turns = _get_turn_spans(exporter)
    assert len(turns) == 1
    assert turns[0]["role"] == "assistant"

    # Verify usage attributes are set directly on the turn span
    turn_span = turns[0]["_span"]
    turn_attrs = dict(turn_span.attributes)
    assert turn_attrs["yuu.llm.provider"] == "openai"
    assert turn_attrs["yuu.llm.model"] == "gpt-4"
    assert turn_attrs["yuu.llm.usage.input_tokens"] == 10
    assert turn_attrs["yuu.llm.usage.output_tokens"] == 20

    # Verify standalone usage event was also recorded on the turn span
    usage_events = [ev for ev in turn_span.events if ev.name == "yuu.llm.usage"]
    assert len(usage_events) == 1


def test_record_llm_usage_accepts_matching_cost() -> None:
    """record_llm_usage(..., cost=...) records both request-level objects."""
    exporter = _make_exporter()

    class FakeUsage:
        provider = "openai"
        model = "gpt-4"
        request_id = "req_123"
        input_tokens = 10
        output_tokens = 20
        cache_read_tokens = 1
        cache_write_tokens = 2
        total_tokens = 33

    class FakeCost:
        total_cost = 0.0123
        source = "yaml"

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        with chat.turn("assistant"):
            ytrace.record_llm_usage(FakeUsage(), cost=FakeCost())

    turn_span = [s for s in exporter.get_finished_spans() if s.name == "turn"][0]
    usage_events = [ev for ev in turn_span.events if ev.name == "yuu.llm.usage"]
    cost_events = [ev for ev in turn_span.events if ev.name == "yuu.cost"]
    assert len(usage_events) == 1
    assert len(cost_events) == 1
    cost_attrs = dict(cost_events[0].attributes)
    assert cost_attrs["yuu.cost.amount"] == 0.0123
    assert cost_attrs["yuu.cost.source"] == "yaml"
    assert cost_attrs["yuu.llm.request_id"] == "req_123"


def test_record_llm_cost_is_first_class_helper() -> None:
    """record_llm_cost() records usage + cost without deprecation warnings."""
    exporter = _make_exporter()

    usage = ytrace.LlmUsageDelta(
        provider="openai",
        model="gpt-4",
        request_id="req_456",
        input_tokens=3,
        output_tokens=4,
    )

    class FakeCost:
        total_cost = 0.0045
        source = "provider"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
            with chat.turn("assistant"):
                ytrace.record_llm_cost(usage, FakeCost())

    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
    turn_span = [s for s in exporter.get_finished_spans() if s.name == "turn"][0]
    assert [ev.name for ev in turn_span.events].count("yuu.llm.usage") == 1
    assert [ev.name for ev in turn_span.events].count("yuu.cost") == 1


def test_turn_serializes_msgspec_structs() -> None:
    """msgspec Structs in turn items are serialized to JSON dicts."""
    exporter = _make_exporter()

    class Item(msgspec.Struct, frozen=True):
        type: str
        value: int

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        with chat.turn("assistant") as t:
            t.add(Item(type="x", value=1))

    turns = _get_turn_spans(exporter)
    assert turns[0]["items"] == [{"type": "x", "value": 1}]


def test_turn_serializes_yuullm_message_content() -> None:
    """yuullm.Message structs are flattened to renderable content items."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user(yuullm.user("hello", name="turn-1"))
        with chat.turn("assistant") as t:
            t.add(
                yuullm.assistant(
                    "checking",
                    yuullm.tool_call_item(
                        yuullm.ToolCall(
                            id="call_1",
                            name="search",
                            arguments='{"q":"x"}',
                        )
                    ),
                )
            )
        with chat.turn("tool") as t:
            t.add(yuullm.tool("call_1", "done"))

    turns = _get_turn_spans(exporter)
    assert turns[0]["items"] == [{"type": "text", "text": "hello"}]
    assert turns[1]["items"] == [
        {"type": "text", "text": "checking"},
        {
            "type": "tool_call",
            "id": "call_1",
            "name": "search",
            "arguments": '{"q":"x"}',
        },
    ]
    assert turns[2]["items"] == [
        {"type": "tool_result", "tool_call_id": "call_1", "content": "done"}
    ]


def test_multiple_turns_in_order() -> None:
    """Multiple turns are recorded in chronological order."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.user("hello")
        with chat.turn("assistant") as t:
            t.add({"type": "text", "text": "hi there"})
        chat.user("thanks")

    turns = _get_turn_spans(exporter)
    assert len(turns) == 3
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    assert turns[2]["role"] == "user"


# ---------------------------------------------------------------------------
# Tool span tests
# ---------------------------------------------------------------------------


def test_tool_span_records_name_and_output() -> None:
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        with chat.tools() as tools:
            with tools.tool(name="my_tool", call_id="tc_1", input={"x": 1}) as ts:
                ts.ok("result_value")

    spans = exporter.get_finished_spans()
    tool_spans = [s for s in spans if s.name == "tool:my_tool"]
    assert tool_spans, [s.name for s in spans]
    assert tool_spans[0].attributes.get("yuu.tool.name") == "my_tool"
    assert tool_spans[0].attributes.get("yuu.tool.call_id") == "tc_1"
    assert tool_spans[0].attributes.get("yuu.tool.output") == "result_value"


def test_tool_span_records_error() -> None:
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        with chat.tools() as tools:
            with tools.tool(name="bad_tool", call_id="tc_2", input={}) as ts:
                ts.fail("something went wrong")

    spans = exporter.get_finished_spans()
    tool_spans = [s for s in spans if s.name == "tool:bad_tool"]
    assert tool_spans
    assert tool_spans[0].attributes.get("yuu.tool.error") == "something went wrong"


def test_tool_batch_unchanged() -> None:
    """Tool batch spans work as before, unaffected by turn refactoring."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        tools_ctx = chat.start_tools()
        span = tools_ctx.start_tool(name="echo", call_id="tc_1", input={"msg": "hi"})
        span.ok("hi")
        span.end()
        tools_ctx.end()

    spans = exporter.get_finished_spans()
    tool_spans = [s for s in spans if s.name == "tool:echo"]
    assert tool_spans
    tools_wrapper = [s for s in spans if s.name == "tools"]
    assert tools_wrapper


# ---------------------------------------------------------------------------
# Conversation span tests
# ---------------------------------------------------------------------------


def test_conversation_id_propagated_to_child_spans() -> None:
    """Verify conversation_id is set on tools and tool:* spans."""
    exporter = _make_exporter()
    conv_id = uuid.uuid4()

    with ytrace.conversation(id=conv_id, agent="a", model="m") as chat:
        chat.user("hi")
        with chat.tools() as tools:
            with tools.tool(name="noop", call_id="tc_1", input={}) as ts:
                ts.ok("ok")

    spans = exporter.get_finished_spans()
    cid = str(conv_id)
    for span in spans:
        if span.name in ("tools", "tool:noop", "turn"):
            assert span.attributes.get("yuu.conversation.id") == cid, (
                f"span {span.name} missing conversation_id"
            )


def test_start_conversation_manual_end() -> None:
    """start_conversation() returns a context that must be manually ended."""
    exporter = _make_exporter()

    chat = ytrace.start_conversation(id=uuid.uuid4(), agent="a", model="m")
    chat.user("hello")
    chat.end()

    spans = exporter.get_finished_spans()
    conv_spans = [s for s in spans if s.name == "conversation"]
    assert conv_spans


def test_system_prompt_recorded() -> None:
    """chat.system() emits a 'system' turn span with persona as an item."""
    exporter = _make_exporter()

    with ytrace.conversation(id=uuid.uuid4(), agent="a", model="m") as chat:
        chat.system("You are helpful.", tools=[{"name": "search"}])

    spans = exporter.get_finished_spans()
    system_turn = next(
        (
            s
            for s in spans
            if s.name == "turn" and s.attributes.get("yuu.turn.role") == "system"
        ),
        None,
    )
    assert system_turn is not None, "expected a 'system' turn span"
    items = json.loads(system_turn.attributes.get("yuu.turn.items", "[]"))
    assert items == [{"type": "text", "text": "You are helpful."}]
    tools_raw = system_turn.attributes.get("yuu.context.system.tools")
    assert tools_raw is not None
    assert json.loads(tools_raw) == [{"name": "search"}]


def test_init_memory(_fresh_tracer_provider) -> None:
    """init_memory() returns a store that captures spans."""
    store = ytrace.init_memory()

    conv_id = uuid.uuid4()
    with ytrace.conversation(
        id=conv_id, agent="test-agent", model="test-model"
    ) as chat:
        chat.user("hello")
        with chat.tools() as tools:
            with tools.tool(name="echo", call_id="tc_1", input={"msg": "hi"}) as ts:
                ts.ok("hi")

    # Query the store
    all_spans = store.get_all_spans()
    assert len(all_spans) >= 2  # conversation, tools, tool:echo

    conv = store.get_conversation(str(conv_id))
    assert conv is not None
    assert conv["agent"] == "test-agent"

    convs = store.list_conversations()
    assert convs["total"] >= 1
