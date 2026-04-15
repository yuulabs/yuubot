from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger
import yuullm

from yuubot.daemon.llm_trace import LLMTraceContext, wrap_llm_client


class _FakeChoice:
    def __init__(self, finish_reason: str | None = None) -> None:
        self.finish_reason = finish_reason
        self.delta = None


class _FakeChunk:
    def __init__(self, request_id: str, finish_reason: str | None = None) -> None:
        self.id = request_id
        self.choices = [_FakeChoice(finish_reason)]


class _FakeProvider:
    provider = "test"
    api_type = "openai-chat-completion"


class _FakeLLMClient:
    def __init__(
        self,
        items: list[yuullm.StreamItem],
        *,
        usage: yuullm.Usage | None = None,
        raw_chunks: list[Any] | None = None,
    ) -> None:
        self.default_model = "test-model"
        self.provider = _FakeProvider()
        self._items = items
        self._usage = usage
        self._raw_chunks = list(raw_chunks or [])

    async def stream(
        self,
        messages: list[yuullm.Message],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        del messages, model, tools, kwargs
        for chunk in self._raw_chunks:
            if on_raw_chunk is not None:
                on_raw_chunk(chunk)

        async def _iter() -> AsyncIterator[yuullm.StreamItem]:
            for item in self._items:
                yield item

        return _iter(), yuullm.Store(usage=self._usage)


@contextmanager
def _capture_log_messages(level: str = "DEBUG") -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, level=level, format="{level} {message}")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


async def test_llm_trace_logs_request_lifecycle() -> None:
    usage = yuullm.Usage(
        provider="test",
        model="test-model",
        request_id="usage-req-1",
        input_tokens=12,
        output_tokens=7,
        total_tokens=19,
    )
    base = _FakeLLMClient(
        [yuullm.Response(item={"type": "text", "text": "pong"})],
        usage=usage,
        raw_chunks=[_FakeChunk("raw-req-1", "stop")],
    )
    llm = wrap_llm_client(
        base,
        trace=LLMTraceContext(
            ctx_id=2,
            runtime_id="yuubot-main-2",
            task_id="task-123",
            agent_name="main",
        ),
    )

    with _capture_log_messages() as logs:
        stream, store = await llm.stream([yuullm.user("ping")], tools=[{"name": "noop"}])
        items = [item async for item in stream]

    assert len(items) == 1
    assert store.usage == usage
    joined = "\n".join(logs)
    assert "INFO LLM request started: ctx=2 runtime_id=yuubot-main-2 task_id=task-123 agent=main call=1" in joined
    assert "DEBUG LLM first raw chunk: ctx=2 runtime_id=yuubot-main-2 call=1 raw_type=_FakeChunk request_id=raw-req-1 finish_reason=stop" in joined
    assert "INFO LLM request ended: ctx=2 runtime_id=yuubot-main-2 task_id=task-123 agent=main call=1 status=completed" in joined
    assert "responses=1 text_chars=4 tool_calls=0" in joined
    assert "usage=in=12 out=7 total=19 request_id=usage-req-1" in joined


async def test_llm_trace_warns_when_tool_followup_has_no_output() -> None:
    usage = yuullm.Usage(
        provider="test",
        model="test-model",
        request_id="usage-req-2",
        input_tokens=20,
        output_tokens=0,
        total_tokens=20,
    )
    base = _FakeLLMClient(
        [],
        usage=usage,
        raw_chunks=[_FakeChunk("raw-req-2", "stop")],
    )
    llm = wrap_llm_client(
        base,
        trace=LLMTraceContext(
            ctx_id=2,
            runtime_id="yuubot-main-2",
            task_id="task-456",
            agent_name="main",
        ),
    )
    history = [
        yuullm.user("do something"),
        yuullm.assistant(
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "call_cap_cli",
                "arguments": "{}",
            }
        ),
        yuullm.tool("call-1", "tool ok"),
    ]

    with _capture_log_messages() as logs:
        stream, _store = await llm.stream(history)
        items = [item async for item in stream]

    assert items == []
    joined = "\n".join(logs)
    assert "INFO LLM request started: ctx=2 runtime_id=yuubot-main-2 task_id=task-456 agent=main call=1" in joined
    assert "after_tool_result=True" in joined
    assert "WARNING LLM stream produced no assistant output: ctx=2 runtime_id=yuubot-main-2 task_id=task-456 agent=main call=1" in joined
    assert "finish_reason=stop" in joined
    assert "usage=in=20 out=0 total=20" in joined
