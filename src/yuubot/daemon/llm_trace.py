"""LLM request lifecycle logging for yuubot agent runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any

import attrs
from loguru import logger
import yuullm


@attrs.define(frozen=True)
class LLMTraceContext:
    ctx_id: int | None
    runtime_id: str
    task_id: str
    agent_name: str


@attrs.define
class _RawStreamStats:
    chunk_count: int = 0
    first_type: str = ""
    last_type: str = ""
    request_id: str = ""
    finish_reasons: list[str] = attrs.field(factory=list)

    def observe(self, chunk: Any) -> None:
        self.chunk_count += 1
        chunk_type = _chunk_type(chunk)
        if not self.first_type:
            self.first_type = chunk_type
        self.last_type = chunk_type

        request_id = _chunk_request_id(chunk)
        if request_id and not self.request_id:
            self.request_id = request_id

        finish_reason = _chunk_finish_reason(chunk)
        if finish_reason and finish_reason not in self.finish_reasons:
            self.finish_reasons.append(finish_reason)


@attrs.define
class _StreamItemStats:
    reasoning_items: int = 0
    response_items: int = 0
    response_text_chars: int = 0
    tool_calls: int = 0
    ticks: int = 0

    def observe(self, item: yuullm.StreamItem) -> None:
        if isinstance(item, yuullm.Reasoning):
            self.reasoning_items += 1
            return
        if isinstance(item, yuullm.Response):
            self.response_items += 1
            self.response_text_chars += len(_item_text(item.item))
            return
        if isinstance(item, yuullm.ToolCall):
            self.tool_calls += 1
            return
        if isinstance(item, yuullm.Tick):
            self.ticks += 1


@attrs.define
class TracedLLMClient:
    """Wrap a yuullm-compatible client and emit request lifecycle logs."""

    client: Any
    trace: LLMTraceContext
    _call_seq: int = 0

    @property
    def default_model(self) -> str:
        return str(getattr(self.client, "default_model", "") or "")

    @property
    def provider(self) -> Any:
        return getattr(self.client, "provider", None)

    async def list_models(self) -> list[yuullm.ProviderModel]:
        return await self.client.list_models()

    async def stream(
        self,
        messages: list[yuullm.Message],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        self._call_seq += 1
        call_id = self._call_seq
        provider_name = _provider_name(self.provider)
        api_type = _provider_api_type(self.provider)
        effective_model = str(model or self.default_model or "")
        raw_stats = _RawStreamStats()
        stream_stats = _StreamItemStats()
        started_at = monotonic()
        after_tool_result = _history_ends_with_tool_result(messages)

        logger.info(
            "LLM request started: ctx={} runtime_id={} task_id={} agent={} call={} provider={} api={} model={} messages={} tools={} after_tool_result={} history_roles={}",
            self.trace.ctx_id,
            self.trace.runtime_id,
            self.trace.task_id,
            self.trace.agent_name,
            call_id,
            provider_name,
            api_type,
            effective_model,
            len(messages),
            len(tools or []),
            after_tool_result,
            _summarize_message_roles(messages),
        )

        def _raw_hook(chunk: Any) -> None:
            raw_stats.observe(chunk)
            if raw_stats.chunk_count == 1:
                logger.debug(
                    "LLM first raw chunk: ctx={} runtime_id={} call={} raw_type={} request_id={} finish_reason={}",
                    self.trace.ctx_id,
                    self.trace.runtime_id,
                    call_id,
                    raw_stats.first_type or "?",
                    raw_stats.request_id or "-",
                    _finish_reasons_text(raw_stats.finish_reasons),
                )
            if on_raw_chunk is not None:
                on_raw_chunk(chunk)

        try:
            stream, store = await self.client.stream(
                messages,
                model=model,
                tools=tools,
                on_raw_chunk=_raw_hook,
                **kwargs,
            )
        except Exception as exc:
            elapsed = monotonic() - started_at
            logger.warning(
                "LLM request failed before stream: ctx={} runtime_id={} task_id={} agent={} call={} provider={} api={} model={} elapsed={:.2f}s error={} status_code={} request_id={}",
                self.trace.ctx_id,
                self.trace.runtime_id,
                self.trace.task_id,
                self.trace.agent_name,
                call_id,
                provider_name,
                api_type,
                effective_model,
                elapsed,
                type(exc).__name__,
                _exception_status_code(exc) or "-",
                _exception_request_id(exc) or raw_stats.request_id or "-",
            )
            raise

        async def _wrapped() -> AsyncIterator[yuullm.StreamItem]:
            status = "completed"
            try:
                async for item in stream:
                    stream_stats.observe(item)
                    yield item
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            except Exception as exc:
                status = "stream_error"
                logger.warning(
                    "LLM stream failed: ctx={} runtime_id={} task_id={} agent={} call={} provider={} api={} model={} raw_chunks={} error={} status_code={} request_id={}",
                    self.trace.ctx_id,
                    self.trace.runtime_id,
                    self.trace.task_id,
                    self.trace.agent_name,
                    call_id,
                    provider_name,
                    api_type,
                    effective_model,
                    raw_stats.chunk_count,
                    type(exc).__name__,
                    _exception_status_code(exc) or "-",
                    _exception_request_id(exc) or raw_stats.request_id or "-",
                )
                raise
            finally:
                elapsed = monotonic() - started_at
                usage = store.usage
                logger.info(
                    "LLM request ended: ctx={} runtime_id={} task_id={} agent={} call={} status={} elapsed={:.2f}s provider={} api={} model={} raw_chunks={} raw_types={}->{} finish_reason={} reasoning={} responses={} text_chars={} tool_calls={} ticks={} usage={} request_id={}",
                    self.trace.ctx_id,
                    self.trace.runtime_id,
                    self.trace.task_id,
                    self.trace.agent_name,
                    call_id,
                    status,
                    elapsed,
                    provider_name,
                    api_type,
                    effective_model,
                    raw_stats.chunk_count,
                    raw_stats.first_type or "-",
                    raw_stats.last_type or "-",
                    _finish_reasons_text(raw_stats.finish_reasons),
                    stream_stats.reasoning_items,
                    stream_stats.response_items,
                    stream_stats.response_text_chars,
                    stream_stats.tool_calls,
                    stream_stats.ticks,
                    _format_usage(usage),
                    (getattr(usage, "request_id", None) or raw_stats.request_id or "-"),
                )
                if status == "completed" and stream_stats.response_items == 0 and stream_stats.tool_calls == 0:
                    logger.warning(
                        "LLM stream produced no assistant output: ctx={} runtime_id={} task_id={} agent={} call={} provider={} api={} model={} after_tool_result={} raw_chunks={} finish_reason={} usage={}",
                        self.trace.ctx_id,
                        self.trace.runtime_id,
                        self.trace.task_id,
                        self.trace.agent_name,
                        call_id,
                        provider_name,
                        api_type,
                        effective_model,
                        after_tool_result,
                        raw_stats.chunk_count,
                        _finish_reasons_text(raw_stats.finish_reasons),
                        _format_usage(usage),
                    )

        return _wrapped(), store

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


def wrap_llm_client(client: Any, *, trace: LLMTraceContext) -> Any:
    if isinstance(client, TracedLLMClient):
        return client
    return TracedLLMClient(client=client, trace=trace)


def _provider_name(provider: Any) -> str:
    value = getattr(provider, "provider", None)
    if isinstance(value, str) and value:
        return value
    return type(provider).__name__ if provider is not None else "unknown"


def _provider_api_type(provider: Any) -> str:
    value = getattr(provider, "api_type", None)
    if isinstance(value, str) and value:
        return value
    return "unknown"


def _chunk_type(chunk: Any) -> str:
    value = getattr(chunk, "type", None)
    if isinstance(value, str) and value:
        return value
    return type(chunk).__name__


def _chunk_request_id(chunk: Any) -> str:
    chunk_id = getattr(chunk, "id", None)
    if isinstance(chunk_id, str) and chunk_id:
        return chunk_id
    message = getattr(chunk, "message", None)
    msg_id = getattr(message, "id", None)
    if isinstance(msg_id, str) and msg_id:
        return msg_id
    return ""


def _chunk_finish_reason(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None)
    if choices:
        choice = choices[0]
        value = getattr(choice, "finish_reason", None)
        if isinstance(value, str) and value:
            return value

    delta = getattr(chunk, "delta", None)
    if delta is not None:
        value = getattr(delta, "stop_reason", None)
        if isinstance(value, str) and value:
            return value

    message = getattr(chunk, "message", None)
    if message is not None:
        value = getattr(message, "stop_reason", None)
        if isinstance(value, str) and value:
            return value
    return ""


def _finish_reasons_text(finish_reasons: list[str]) -> str:
    return ",".join(finish_reasons) if finish_reasons else "-"


def _item_text(item: Any) -> str:
    if isinstance(item, dict) and item.get("type") == "text":
        return str(item.get("text", ""))
    return ""


def _summarize_message_roles(messages: list[yuullm.Message], *, limit: int = 4) -> str:
    if not messages:
        return "empty"
    roles = [message[0] for message in messages[-limit:]]
    summary = ",".join(roles)
    if len(messages) > limit:
        return f"+{len(messages) - limit},{summary}"
    return summary


def _history_ends_with_tool_result(messages: list[yuullm.Message]) -> bool:
    if not messages:
        return False
    return messages[-1][0] == "tool"


def _format_usage(usage: yuullm.Usage | None) -> str:
    if usage is None:
        return "none"
    parts = [
        f"in={int(getattr(usage, 'input_tokens', 0) or 0)}",
        f"out={int(getattr(usage, 'output_tokens', 0) or 0)}",
        f"total={int(getattr(usage, 'total_tokens', 0) or 0)}",
    ]
    cache_read = int(getattr(usage, "cache_read_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_write_tokens", 0) or 0)
    if cache_read:
        parts.append(f"cache_read={cache_read}")
    if cache_write:
        parts.append(f"cache_write={cache_write}")
    return " ".join(parts)


def _exception_status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    return value if isinstance(value, int) else None


def _exception_request_id(exc: Exception) -> str:
    value = getattr(exc, "request_id", None)
    if isinstance(value, str) and value:
        return value
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for key in ("x-request-id", "request-id"):
            header_value = headers.get(key)
            if isinstance(header_value, str) and header_value:
                return header_value
    return ""
