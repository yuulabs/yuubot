"""Tests for YuuSession: append validation, fallback chain, history, client integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yuullm.client import YLLMClient
from yuullm.pool import ProviderPool
from yuullm.session import YuuSession
from yuullm.types import (
    AttemptRecovery,
    CallRecord,
    Message,
    ModelBinding,
    Reasoning,
    Response,
    Store,
    ThinkingBlock,
    Tick,
    ToolCall,
    Usage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a MagicMock-spec'd ProviderPool."""
    pool = MagicMock(spec=ProviderPool)
    pool.resolve = AsyncMock()
    pool.get_client = MagicMock()
    pool.record = MagicMock()
    pool.invalidate = MagicMock()
    pool.supports_seamless_recovery = MagicMock(return_value=False)
    return pool


@pytest.fixture
def mock_client_factory():
    """Fixture producing fresh mock client instances with stream support."""

    def _make(stream_return=_default_stream, store=None):
        client = MagicMock()
        client.stream = AsyncMock(return_value=(stream_return, store or Store()))
        return client

    return _make


async def _default_stream():
    """Default stream helper: yield a single text Response."""
    yield Response(item={"type": "text", "text": "hello"})


async def _make_stream(*items):
    """Convert items to an async iterator for mock stream returns."""
    for item in items:
        yield item


async def _make_stream_then_error(*items, error):
    """Yield items, then raise *error* from inside the stream."""
    for item in items:
        yield item
    raise error


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_session(pool, selector="test-selector", history=None):
    """Create a YuuSession with given pool and optional params."""
    return YuuSession(pool=pool, selector=selector, history=history)


def _text_response(text="hello"):
    """Shorthand for a text Response stream item."""
    return Response(item={"type": "text", "text": text})


def _tool_call(id="tc_1", name="search", arguments='{"q":"test"}'):
    """Shorthand for a ToolCall stream item."""
    return ToolCall(id=id, name=name, arguments=arguments)


def _reasoning(text="hmm..."):
    """Shorthand for a Reasoning stream item."""
    return Reasoning(item={"type": "text", "text": text})


def _thinking_block(thinking="Let me think...", signature="sig123"):
    """Shorthand for a ThinkingBlock stream item."""
    return ThinkingBlock(thinking=thinking, signature=signature)


def _binding(name="p1", model="m1", source="exact"):
    """Shorthand for a ModelBinding."""
    return ModelBinding(provider_name=name, model=model, source=source)


# ---------------------------------------------------------------------------
# YuuSession.append() tests
# ---------------------------------------------------------------------------


def test_append_adds_message(mock_pool) -> None:
    """append() adds message to history."""
    session = _make_session(mock_pool)
    msg = Message(role="user", content=[{"type": "text", "text": "hello"}])

    session.append(msg)

    assert len(session.history) == 1
    assert session.history[0] is msg
    assert session.history[0].role == "user"


def test_append_consecutive_user_raises(mock_pool) -> None:
    """Two user messages in a row → ValueError."""
    session = _make_session(mock_pool)
    msg1 = Message(role="user", content=[{"type": "text", "text": "first"}])
    msg2 = Message(role="user", content=[{"type": "text", "text": "second"}])

    session.append(msg1)

    with pytest.raises(ValueError, match="consecutive user messages"):
        session.append(msg2)


def test_append_tool_without_tool_calls_raises(mock_pool) -> None:
    """Tool message without prior assistant tool_calls → ValueError."""
    session = _make_session(mock_pool)

    # Add a user message first so history is non-empty
    session.append(Message(role="user", content=[{"type": "text", "text": "hi"}]))

    tool_msg = Message(
        role="tool",
        content=[{"type": "tool_result", "tool_call_id": "tc_1", "content": "result"}],
    )

    # Tool message after user → ValueError (needs assistant with tool_calls)
    with pytest.raises(ValueError, match="tool message must follow an assistant"):
        session.append(tool_msg)


def test_append_tool_after_assistant_with_tool_calls_succeeds(mock_pool) -> None:
    """Tool message after assistant with tool_calls → accepted."""
    session = _make_session(mock_pool)

    # Assistant with tool_call in content
    assistant_msg = Message(
        role="assistant",
        content=[
            {"type": "text", "text": "let me search"},
            {
                "type": "tool_call",
                "id": "tc_1",
                "name": "search",
                "arguments": '{"q":"x"}',
            },
        ],
    )
    session.append(assistant_msg)

    tool_msg = Message(
        role="tool",
        content=[{"type": "tool_result", "tool_call_id": "tc_1", "content": "done"}],
    )
    session.append(tool_msg)

    assert len(session.history) == 2
    assert session.history[1].role == "tool"


def test_append_multiple_tool_results_can_close_multiple_calls(mock_pool) -> None:
    """Multiple tool results may arrive as consecutive tool messages."""
    session = _make_session(mock_pool)

    session.append(
        Message(
            role="assistant",
            content=[
                {
                    "type": "tool_call",
                    "id": "tc_1",
                    "name": "search",
                    "arguments": "{}",
                },
                {"type": "tool_call", "id": "tc_2", "name": "calc", "arguments": "{}"},
            ],
        )
    )
    session.append(
        Message(
            role="tool",
            content=[
                {"type": "tool_result", "tool_call_id": "tc_1", "content": "done"}
            ],
        )
    )
    session.append(
        Message(
            role="tool",
            content=[{"type": "tool_result", "tool_call_id": "tc_2", "content": "42"}],
        )
    )

    assert [msg.role for msg in session.history] == ["assistant", "tool", "tool"]


def test_append_unknown_tool_result_raises(mock_pool) -> None:
    """A tool result must match an open tool call id exactly once."""
    session = _make_session(mock_pool)
    session.append(
        Message(
            role="assistant",
            content=[
                {"type": "tool_call", "id": "tc_1", "name": "search", "arguments": "{}"}
            ],
        )
    )

    with pytest.raises(ValueError, match="does not match an open tool call"):
        session.append(
            Message(
                role="tool",
                content=[
                    {"type": "tool_result", "tool_call_id": "missing", "content": "x"}
                ],
            )
        )


def test_append_duplicate_tool_result_raises(mock_pool) -> None:
    """A single tool message cannot close the same call twice."""
    session = _make_session(mock_pool)
    session.append(
        Message(
            role="assistant",
            content=[
                {"type": "tool_call", "id": "tc_1", "name": "search", "arguments": "{}"}
            ],
        )
    )

    with pytest.raises(ValueError, match="duplicate tool result"):
        session.append(
            Message(
                role="tool",
                content=[
                    {"type": "tool_result", "tool_call_id": "tc_1", "content": "x"},
                    {"type": "tool_result", "tool_call_id": "tc_1", "content": "y"},
                ],
            )
        )


def test_append_tool_after_assistant_without_tool_calls_raises(mock_pool) -> None:
    """Tool message after assistant that has no tool_calls → ValueError."""
    session = _make_session(mock_pool)

    # Assistant without tool_calls
    assistant_msg = Message(
        role="assistant",
        content=[{"type": "text", "text": "just a reply"}],
    )
    session.append(assistant_msg)

    tool_msg = Message(
        role="tool",
        content=[{"type": "tool_result", "tool_call_id": "tc_1", "content": "result"}],
    )

    with pytest.raises(ValueError, match="tool message must follow an assistant"):
        session.append(tool_msg)


def test_append_tool_on_empty_history_raises(mock_pool) -> None:
    """Tool message on empty history → ValueError."""
    session = _make_session(mock_pool)

    tool_msg = Message(
        role="tool",
        content=[{"type": "tool_result", "tool_call_id": "tc_1", "content": "result"}],
    )

    with pytest.raises(ValueError, match="tool message requires a prior assistant"):
        session.append(tool_msg)


# ---------------------------------------------------------------------------
# YuuSession.history property tests
# ---------------------------------------------------------------------------


def test_history_property_returns_list(mock_pool) -> None:
    """history returns the current History list."""
    session = _make_session(mock_pool)
    assert isinstance(session.history, list)
    assert len(session.history) == 0

    msg = Message(role="user", content=[{"type": "text", "text": "hello"}])
    session.append(msg)

    assert len(session.history) == 1
    assert session.history[0] is msg


def test_history_property_is_internal_reference(mock_pool) -> None:
    """history property returns the internal list (documented behaviour)."""
    session = _make_session(mock_pool)
    msg = Message(role="user", content=[{"type": "text", "text": "hello"}])
    session.append(msg)

    hist = session.history
    hist.append(Message(role="system", content=[{"type": "text", "text": "injected"}]))

    # Mutation through the property affects internal state
    assert len(session.history) == 2


# ---------------------------------------------------------------------------
# YLLMClient.create_session() tests
# ---------------------------------------------------------------------------


def test_create_session_returns_yuusession(mock_pool) -> None:
    """create_session() returns YuuSession with correct attributes."""
    mock_provider = MagicMock()
    client = YLLMClient(
        mock_provider, default_model="test-model", auto_prompt_caching=False
    )

    session = client.create_session(pool=mock_pool, selector="test-selector")

    assert isinstance(session, YuuSession)
    assert session._pool is mock_pool
    assert session._selector == "test-selector"
    assert session._history == []


def test_create_session_custom_history(mock_pool) -> None:
    """history parameter is passed through to YuuSession."""
    mock_provider = MagicMock()
    client = YLLMClient(
        mock_provider, default_model="test-model", auto_prompt_caching=False
    )

    history = [Message(role="user", content=[{"type": "text", "text": "hi"}])]

    session = client.create_session(
        pool=mock_pool, selector="test-selector", history=history
    )

    assert len(session.history) == 1
    assert session.history[0].role == "user"
    assert session.history[0].content == [{"type": "text", "text": "hi"}]


# ---------------------------------------------------------------------------
# YuuSession.stream() tests — basic flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_resolves_and_streams(mock_pool) -> None:
    """Mock pool.resolve() and client.stream(), verify stream yields and history accumulates."""
    binding = _binding(name="deepseek", model="deepseek-v4")
    mock_pool.resolve.return_value = [binding]

    mock_client = MagicMock()
    store = Store(
        usage=Usage(
            provider="deepseek", model="deepseek-v4", input_tokens=10, output_tokens=20
        )
    )
    mock_client.stream = AsyncMock(
        return_value=(_make_stream(_text_response("hello world")), store)
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    # Items yielded to caller
    assert len(items) == 1
    assert isinstance(items[0], Response)
    assert items[0].item["text"] == "hello world"

    # History accumulated: one assistant message
    assert len(session.history) == 1
    assistant_msg = session.history[0]
    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == [{"type": "text", "text": "hello world"}]

    # Pool interactions
    mock_pool.resolve.assert_called_once_with("test-selector")
    mock_pool.get_client.assert_called_once_with(binding)
    mock_client.stream.assert_called_once()


@pytest.mark.asyncio
async def test_stream_multiple_response_items_concatenated(mock_pool) -> None:
    """Multiple consecutive Response text items are merged into one content entry."""
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                _text_response("hello "),
                _text_response("world"),
                _text_response("!"),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    # All three tokens are still yielded to the caller
    assert len(items) == 3
    assert len(session.history) == 1
    # Consecutive text chunks are merged into a single content item
    assert session.history[0].content == [
        {"type": "text", "text": "hello world!"},
    ]


# ---------------------------------------------------------------------------
# YuuSession.stream() tests — fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_fallback_chain(mock_pool) -> None:
    """First binding fails (ConnectionError) → invalidate → second binding succeeds."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    # First client raises ConnectionError
    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=ConnectionError("connection refused"))

    # Second client succeeds
    client2 = MagicMock()
    store2 = Store(
        usage=Usage(provider="p2", model="m2", input_tokens=10, output_tokens=20)
    )
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("fallback worked")), store2)
    )

    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 2
    recovery = items[0]
    assert isinstance(recovery, AttemptRecovery)
    assert recovery.failed_provider == "p1"
    assert recovery.next_provider == "p2"
    assert recovery.rollback_to.history_len == 0
    assert recovery.rollback_to.stream_seq == 0
    assert recovery.continuation == "non_seamless"
    assert items[1].item["text"] == "fallback worked"

    # Invalidated the first binding
    mock_pool.invalidate.assert_called_once_with("test-selector", "p1")

    # Both get_client calls happened
    assert mock_pool.get_client.call_count == 2

    # History accumulated from the successful binding
    assert len(session.history) == 1
    assert session.history[0].role == "assistant"


@pytest.mark.asyncio
async def test_stream_midstream_failure_emits_recovery_checkpoint(mock_pool) -> None:
    """A failure after partial output retries and tells caller what to roll back."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    client1 = MagicMock()
    client1.stream = AsyncMock(
        return_value=(
            _make_stream_then_error(
                _text_response("partial"),
                error=ConnectionError("stream dropped"),
            ),
            Store(),
        )
    )

    client2 = MagicMock()
    usage2 = Usage(provider="p2", model="m2")
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("final")), Store(usage=usage2))
    )
    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)
    iterator, store = await session.stream()

    items = []
    async for item in iterator:
        items.append(item)

    assert len(items) == 3
    assert items[0].item["text"] == "partial"
    assert isinstance(items[1], AttemptRecovery)
    assert items[1].rollback_to.history_len == 0
    assert items[1].rollback_to.stream_seq == 0
    assert items[1].continuation == "non_seamless"
    assert items[2].item["text"] == "final"
    assert store.recoveries == [items[1]]
    assert store.usage is usage2
    assert session.history[0].content == [{"type": "text", "text": "final"}]


@pytest.mark.asyncio
async def test_stream_recovery_marks_seamless_next_provider(mock_pool) -> None:
    """Recovery event reports whether the next provider can continue seamlessly."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]
    mock_pool.supports_seamless_recovery.side_effect = lambda provider_name: (
        provider_name == "p2"
    )

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=ConnectionError("refused"))
    client2 = MagicMock()
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("ok")), Store())
    )
    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    recovery = items[0]
    assert isinstance(recovery, AttemptRecovery)
    assert recovery.continuation == "seamless"


@pytest.mark.asyncio
async def test_stream_retryable_triggers_fallback(mock_pool) -> None:
    """TimeoutError → fallback to next binding."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=TimeoutError("timed out"))

    client2 = MagicMock()
    store2 = Store(usage=Usage(provider="p2", model="m2"))
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("ok")), store2)
    )

    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 2
    assert isinstance(items[0], AttemptRecovery)
    mock_pool.invalidate.assert_called_once_with("test-selector", "p1")


@pytest.mark.asyncio
async def test_stream_oserror_triggers_fallback(mock_pool) -> None:
    """OSError (socket error) → fallback to next binding."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=OSError("socket error"))

    client2 = MagicMock()
    store2 = Store(usage=Usage(provider="p2", model="m2"))
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("ok")), store2)
    )

    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 2
    assert isinstance(items[0], AttemptRecovery)
    mock_pool.invalidate.assert_called_once_with("test-selector", "p1")


@pytest.mark.asyncio
async def test_stream_5xx_triggers_fallback(mock_pool) -> None:
    """HTTP 503 error → retryable → fallback."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    # Exception with status_code=503
    exc503 = Exception("service unavailable")
    exc503.status_code = 503

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=exc503)

    client2 = MagicMock()
    store2 = Store(usage=Usage(provider="p2", model="m2"))
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("ok")), store2)
    )

    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 2
    assert isinstance(items[0], AttemptRecovery)
    mock_pool.invalidate.assert_called_once_with("test-selector", "p1")


@pytest.mark.asyncio
async def test_stream_4xx_fails_immediately(mock_pool) -> None:
    """HTTP 400 error → non-retryable → raises immediately without fallback."""
    binding1 = _binding(name="p1", model="m1")
    mock_pool.resolve.return_value = [binding1]

    # Exception with status_code=400
    exc400 = Exception("bad request")
    exc400.status_code = 400

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=exc400)
    mock_pool.get_client.return_value = client1

    session = _make_session(mock_pool)

    with pytest.raises(Exception, match="bad request"):
        stream, _store = await session.stream()
        async for item in stream:
            pass

    # Invalidate should NOT have been called
    mock_pool.invalidate.assert_not_called()


@pytest.mark.asyncio
async def test_stream_4xx_does_not_fallback_to_next(mock_pool) -> None:
    """Even when multiple bindings exist, 4xx raises immediately without trying next."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    exc400 = Exception("bad request")
    exc400.status_code = 400

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=exc400)

    # client2 should never be reached
    mock_pool.get_client.return_value = client1

    session = _make_session(mock_pool)

    with pytest.raises(Exception, match="bad request"):
        stream, _store = await session.stream()
        async for item in stream:
            pass

    # Only first client was attempted
    mock_pool.get_client.assert_called_once()
    mock_pool.invalidate.assert_not_called()


@pytest.mark.asyncio
async def test_stream_all_exhausted_raises(mock_pool) -> None:
    """All bindings fail → RuntimeError."""
    binding = _binding(name="p1", model="m1")
    mock_pool.resolve.return_value = [binding]

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=ConnectionError("connection refused"))
    mock_pool.get_client.return_value = client1

    session = _make_session(mock_pool)

    with pytest.raises(RuntimeError, match="all providers exhausted"):
        stream, _store = await session.stream()
        async for item in stream:
            pass

    mock_pool.invalidate.assert_called_once_with("test-selector", "p1")


# ---------------------------------------------------------------------------
# YuuSession.stream() tests — accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_accumulates_tool_calls(mock_pool) -> None:
    """ToolCall items accumulated into assistant message as dicts."""
    mock_pool.resolve.return_value = [_binding()]

    tc = _tool_call(id="tc_1", name="search", arguments='{"q":"test"}')
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(_text_response("let me search"), tc, _text_response("done")),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 3
    assert len(session.history) == 1
    assistant_msg = session.history[0]
    assert assistant_msg.role == "assistant"

    # Content should include text responses and tool_call dicts
    assert len(assistant_msg.content) == 3
    assert assistant_msg.content[0] == {"type": "text", "text": "let me search"}
    assert assistant_msg.content[1] == {
        "type": "tool_call",
        "id": "tc_1",
        "name": "search",
        "arguments": '{"q":"test"}',
    }
    assert assistant_msg.content[2] == {"type": "text", "text": "done"}


@pytest.mark.asyncio
async def test_stream_accumulates_reasoning_as_transient(mock_pool) -> None:
    """Reasoning items are yielded but NOT stored in history (transient)."""
    mock_pool.resolve.return_value = [_binding()]

    reason = _reasoning("let me think about this...")
    response = _text_response("here is the answer")

    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(return_value=(_make_stream(reason, response), store))
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    # Both items yielded to caller
    assert len(items) == 2
    assert isinstance(items[0], Reasoning)
    assert isinstance(items[1], Response)

    # History: assistant message contains only the Response, NOT the Reasoning
    assert len(session.history) == 1
    assistant_msg = session.history[0]
    assert assistant_msg.content == [{"type": "text", "text": "here is the answer"}]


@pytest.mark.asyncio
async def test_stream_reasoning_only_no_history_appended(mock_pool) -> None:
    """If only Reasoning items are streamed (no Response), no assistant message is appended."""
    mock_pool.resolve.return_value = [_binding()]

    reason = _reasoning("thinking only, no output")
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(return_value=(_make_stream(reason), store))
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 1

    # pending_content was empty → no assistant message appended
    assert len(session.history) == 0


@pytest.mark.asyncio
async def test_stream_accumulates_thinking_block(mock_pool) -> None:
    """ThinkingBlock items are stored as content items inside the assistant message."""
    mock_pool.resolve.return_value = [_binding()]

    tb = _thinking_block(
        thinking="Let me work through this step by step.", signature="sig123"
    )
    response = _text_response("final answer")

    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(return_value=(_make_stream(tb, response), store))
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(items) == 2
    assert isinstance(items[0], ThinkingBlock)

    # History: assistant message contains thinking block + text
    assert len(session.history) == 1
    assistant_msg = session.history[0]
    assert len(assistant_msg.content) == 2

    # First item is the thinking block rendered as a message item
    thinking_item = assistant_msg.content[0]
    assert thinking_item["type"] == "thinking"
    assert thinking_item["thinking"] == "Let me work through this step by step."
    assert thinking_item["signature"] == "sig123"

    # Second item is the text response
    assert assistant_msg.content[1] == {"type": "text", "text": "final answer"}


@pytest.mark.asyncio
async def test_stream_ticks_are_ignored(mock_pool) -> None:
    """Tick items are yielded but not stored in history."""
    mock_pool.resolve.return_value = [_binding()]

    tick = Tick()
    response = _text_response("result")

    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(_make_stream(tick, tick, response), store)
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    # All 3 items yielded (including ticks)
    assert len(items) == 3

    # History: only the response, no tick artifacts
    assert len(session.history) == 1
    assert session.history[0].content == [{"type": "text", "text": "result"}]


# ---------------------------------------------------------------------------
# YuuSession.stream() tests — reasoning aggregation & text merging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_aggregates_reasoning_and_merges_text(mock_pool) -> None:
    """OpenAI/DeepSeek order: Reasoning×N → Response×N → ThinkingBlock (at END).

    Reasoning text is aggregated into a buffer and finalized by the trailing
    ThinkingBlock (which contributes only metadata).  Consecutive Response
    text chunks are merged.  Thinking ends up before text despite the
    ThinkingBlock arriving last.
    """
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                _reasoning("The user "),
                _reasoning("said hello."),
                _text_response("Hey"),
                _text_response(" there"),
                _text_response("!"),
                _thinking_block(thinking="unused", signature=None),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    assert len(session.history) == 1
    msg = session.history[0]
    assert len(msg.content) == 2

    # Thinking text comes from the Reasoning buffer, NOT from ThinkingBlock.thinking
    assert msg.content[0]["type"] == "thinking"
    assert msg.content[0]["thinking"] == "The user said hello."

    # Text chunks merged into a single item
    assert msg.content[1]["type"] == "text"
    assert msg.content[1]["text"] == "Hey there!"


@pytest.mark.asyncio
async def test_stream_anthropic_order_with_signature(mock_pool) -> None:
    """Anthropic order: Reasoning → ThinkingBlock(sig) → Response.

    ThinkingBlock arrives inline (before the text) carrying the signature
    needed for round-tripping.  The thinking text still comes from the
    Reasoning buffer.
    """
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                _reasoning("Let me think."),
                _thinking_block(thinking="unused", signature="sig123"),
                _text_response("answer"),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    msg = session.history[0]
    assert len(msg.content) == 2
    assert msg.content[0]["type"] == "thinking"
    assert msg.content[0]["thinking"] == "Let me think."
    assert msg.content[0]["signature"] == "sig123"
    assert msg.content[1] == {"type": "text", "text": "answer"}


@pytest.mark.asyncio
async def test_stream_multiple_thinking_blocks(mock_pool) -> None:
    """Anthropic may emit several thinking blocks, each with its own signature."""
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                _reasoning("first "),
                _thinking_block(thinking="unused", signature="s1"),
                _reasoning("second"),
                _thinking_block(thinking="unused", signature="s2"),
                _text_response("result"),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    msg = session.history[0]
    assert len(msg.content) == 3
    assert msg.content[0] == {"type": "thinking", "thinking": "first ", "signature": "s1"}
    assert msg.content[1] == {"type": "thinking", "thinking": "second", "signature": "s2"}
    assert msg.content[2] == {"type": "text", "text": "result"}


@pytest.mark.asyncio
async def test_stream_redacted_thinking(mock_pool) -> None:
    """Anthropic redacted thinking emits no Reasoning; ThinkingBlock carries redacted_data."""
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                ThinkingBlock(thinking="", redacted_data="encrypted_data"),
                _text_response("answer"),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    msg = session.history[0]
    assert len(msg.content) == 2
    assert msg.content[0] == {"type": "redacted_thinking", "data": "encrypted_data"}
    assert msg.content[1] == {"type": "text", "text": "answer"}


@pytest.mark.asyncio
async def test_stream_merges_consecutive_text_only(mock_pool) -> None:
    """Multiple text chunks with no thinking merge into a single content item."""
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                _text_response("Hello"),
                _text_response(" world"),
                _text_response("!"),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    msg = session.history[0]
    assert len(msg.content) == 1
    assert msg.content[0]["text"] == "Hello world!"


@pytest.mark.asyncio
async def test_stream_does_not_merge_text_across_tool_call(mock_pool) -> None:
    """Text items separated by a ToolCall are NOT consecutive and must not merge."""
    mock_pool.resolve.return_value = [_binding()]
    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(
            _make_stream(
                _text_response("let me search"),
                _tool_call(id="tc_1", name="search", arguments='{"q":"x"}'),
                _text_response("done"),
            ),
            store,
        )
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    items = []
    stream, _store = await session.stream()
    async for item in stream:
        items.append(item)

    msg = session.history[0]
    assert len(msg.content) == 3
    assert msg.content[0] == {"type": "text", "text": "let me search"}
    assert msg.content[1] == {
        "type": "tool_call",
        "id": "tc_1",
        "name": "search",
        "arguments": '{"q":"x"}',
    }
    assert msg.content[2] == {"type": "text", "text": "done"}


# ---------------------------------------------------------------------------
# YuuSession.stream() tests — overrides & metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_overrides_passthrough(mock_pool) -> None:
    """**overrides passed through to client.stream()."""
    mock_pool.resolve.return_value = [_binding(model="deepseek-v4")]

    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(_make_stream(_text_response("ok")), store)
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    stream, _store = await session.stream(
        temperature=0.7, max_tokens=100, on_raw_chunk=lambda c: None
    )
    async for item in stream:
        pass

    # Verify overrides forwarded to client.stream()
    mock_client.stream.assert_called_once()
    call_kwargs = mock_client.stream.call_args[1]
    assert call_kwargs["model"] == "deepseek-v4"
    assert call_kwargs["temperature"] == 0.7
    assert call_kwargs["max_tokens"] == 100
    assert "on_raw_chunk" in call_kwargs


@pytest.mark.asyncio
async def test_stream_rejects_model_override(mock_pool) -> None:
    """The session selector owns model resolution; per-call model override is invalid."""
    session = _make_session(mock_pool)

    with pytest.raises(ValueError, match="model override"):
        await session.stream(model="other-model")


@pytest.mark.asyncio
async def test_stream_rejects_pending_tool_results(mock_pool) -> None:
    """A new LLM attempt cannot start while tool calls are still open."""
    session = _make_session(mock_pool)
    session.append(
        Message(
            role="assistant",
            content=[
                {"type": "tool_call", "id": "tc_1", "name": "search", "arguments": "{}"}
            ],
        )
    )

    with pytest.raises(ValueError, match="tool results are pending"):
        stream, _store = await session.stream()
        async for item in stream:
            pass


@pytest.mark.asyncio
async def test_stream_records_call_record(mock_pool) -> None:
    """Pool.record() called with CallRecord after successful stream."""
    mock_pool.resolve.return_value = [_binding(name="deepseek", model="deepseek-v4")]

    usage = Usage(
        provider="deepseek",
        model="deepseek-v4",
        request_id="req-123",
        input_tokens=50,
        output_tokens=100,
        total_tokens=150,
    )
    store = Store(usage=usage)

    mock_client = MagicMock()
    mock_client.stream = AsyncMock(
        return_value=(_make_stream(_text_response("hello")), store)
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)

    stream, _store = await session.stream()
    async for item in stream:
        pass

    # Verify record() called exactly once
    mock_pool.record.assert_called_once()
    call_record = mock_pool.record.call_args[0][0]

    assert isinstance(call_record, CallRecord)
    assert call_record.provider_name == "deepseek"
    assert call_record.model == "deepseek-v4"
    assert call_record.selector == "test-selector"
    assert call_record.started_at > 0
    assert call_record.finished_at is not None
    assert call_record.finished_at >= call_record.started_at
    assert call_record.usage is usage
    assert call_record.error is None


@pytest.mark.asyncio
async def test_stream_records_call_record_on_fallback(mock_pool) -> None:
    """CallRecord recorded for the successful binding after fallback."""
    binding1 = _binding(name="p1", model="m1")
    binding2 = _binding(name="p2", model="m2")
    mock_pool.resolve.return_value = [binding1, binding2]

    # First client fails
    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=ConnectionError("refused"))

    # Second client succeeds
    usage2 = Usage(provider="p2", model="m2", input_tokens=5, output_tokens=15)
    store2 = Store(usage=usage2)
    client2 = MagicMock()
    client2.stream = AsyncMock(
        return_value=(_make_stream(_text_response("ok")), store2)
    )

    mock_pool.get_client.side_effect = [client1, client2]

    session = _make_session(mock_pool)

    stream, _store = await session.stream()
    async for item in stream:
        pass

    # Record called once for the successful binding
    mock_pool.record.assert_called_once()
    call_record = mock_pool.record.call_args[0][0]
    assert call_record.provider_name == "p2"
    assert call_record.model == "m2"
    assert call_record.error is None


@pytest.mark.asyncio
async def test_stream_no_record_on_all_fail(mock_pool) -> None:
    """When all bindings fail, no CallRecord is recorded."""
    mock_pool.resolve.return_value = [_binding(name="p1", model="m1")]

    client1 = MagicMock()
    client1.stream = AsyncMock(side_effect=ConnectionError("refused"))
    mock_pool.get_client.return_value = client1

    session = _make_session(mock_pool)

    with pytest.raises(RuntimeError):
        stream, _store = await session.stream()
        async for item in stream:
            pass

    # No successful stream → no record
    mock_pool.record.assert_not_called()


# ---------------------------------------------------------------------------
# YuuSession.stream() tests — history snapshot isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_uses_history_snapshot(mock_pool) -> None:
    """Stream passes a shallow copy of history, isolating concurrent mutations."""
    mock_pool.resolve.return_value = [_binding()]

    # Pre-populate history
    initial_msg = Message(role="user", content=[{"type": "text", "text": "initial"}])

    mock_client = MagicMock()
    store = Store(usage=Usage(provider="p1", model="m1"))
    mock_client.stream = AsyncMock(
        return_value=(_make_stream(_text_response("reply")), store)
    )
    mock_pool.get_client.return_value = mock_client

    session = _make_session(mock_pool)
    session.append(initial_msg)

    stream, _store = await session.stream()
    async for item in stream:
        pass

    # client.stream was called with a copy of history containing only the initial message
    history_passed = mock_client.stream.call_args[0][0]
    assert len(history_passed) == 1
    assert history_passed[0].content == [{"type": "text", "text": "initial"}]
    # The copy is not the same list object as session._history
    assert history_passed is not session._history
