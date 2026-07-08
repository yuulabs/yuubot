"""Harness: validates, deserializes, and concurrently executes tool calls.

A Harness lives for exactly one ``run_loop``. Every failure mode — validation
error, execution error, timeout, interrupt — is converted into a ``ToolResult``
handed back to the model; nothing propagates to the conversation.
"""

import asyncio
from contextvars import Token
from typing import cast

import msgspec
from attrs import define, field

from ..domain.messages import ContentItem, ConversationContext, ToolResult
from ..domain.stream import StreamEvent, ToolCall, ToolResultEndPayload
from ..runtime.event_payloads import ConversationStreamPayload, EmitFn
from ..tools import Tool, ToolConfig, build_tools
from ..tools.progress import ToolProgress, bind_progress
from ..util.secrets import redact_value

TOOL_TIMEOUT_S = 240


class HarnessConfig(msgspec.Struct, frozen=True):
    tools: dict[str, ToolConfig] = msgspec.field(default_factory=dict)


@define
class Harness:
    tools: dict[str, Tool]
    emit: EmitFn
    conversation_id: str
    prepare_tasks: dict[str, asyncio.Task[None]] = field(factory=dict)

    @classmethod
    def from_config(cls, config: HarnessConfig, context: ConversationContext, runtime: Runtime) -> "Harness":
        tools = build_tools(config.tools, context, runtime)
        return cls(
            tools=tools,
            emit=runtime.emit,
            conversation_id=context.conversation_id,
            prepare_tasks={name: asyncio.create_task(tool.prepare()) for name, tool in tools.items()},
        )

    async def gather(
        self,
        tool_calls: list[ToolCall],
        stop_event: asyncio.Event,
        timeout: float = TOOL_TIMEOUT_S,
    ) -> list[ToolResult]:
        if not tool_calls:
            return []
        tasks = {asyncio.create_task(self._run_one(call, timeout)): call for call in tool_calls}
        stop_task = asyncio.create_task(stop_event.wait())
        pending = set(tasks)
        results: dict[str, ToolResult] = {}
        try:
            while pending:
                done, _ = await asyncio.wait([*pending, stop_task], return_when=asyncio.FIRST_COMPLETED)
                finished = {cast(asyncio.Task[ToolResult], task) for task in done if task is not stop_task}
                pending -= finished
                for task in finished:
                    results[tasks[task].id] = task.result()
                if stop_task in done:
                    interrupted = set(pending)
                    for task in interrupted:
                        task.cancel()
                    await asyncio.gather(*interrupted, return_exceptions=True)
                    for task in interrupted:
                        call = tasks[task]
                        result = _result(call.id, _with_partial("[system] tool call interrupted.", task))
                        results[call.id] = result
                        self._emit_tool_result_end(call, result)
                    break
        finally:
            stop_task.cancel()
        return [results[call.id] for call in tool_calls]

    async def close(self) -> None:
        pending_prepare = [task for task in self.prepare_tasks.values() if not task.done()]
        for task in pending_prepare:
            task.cancel()
        if pending_prepare:
            await asyncio.gather(*pending_prepare, return_exceptions=True)
        completed_prepare = [task for task in self.prepare_tasks.values() if task.done()]
        if completed_prepare:
            await asyncio.gather(*completed_prepare, return_exceptions=True)
        for tool in self.tools.values():
            await tool.close()

    async def _run_one(self, call: ToolCall, timeout: float) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            result = _result(call.id, f"unknown tool: {call.name}")
            self._emit_tool_result_end(call, result)
            return result
        try:
            raw = msgspec.json.decode((call.arguments or "{}").encode(), type=dict[str, object])
            payload = msgspec.convert(raw, tool.payload_type)
        except msgspec.DecodeError as exc:
            result = _result(call.id, f"invalid JSON for {call.name}: {exc}")
            self._emit_tool_result_end(call, result)
            return result
        except msgspec.ValidationError as exc:
            result = _result(call.id, f"invalid payload for {call.name}: {exc}")
            self._emit_tool_result_end(call, result)
            return result
        try:
            await self._wait_prepared(call.name)
        except Exception as exc:
            result = _result(call.id, f"{call.name} prepare failed: {_exception_detail(exc)}")
            self._emit_tool_result_end(call, result)
            return result
        progress_token = _bind_tool_progress(
            self.emit,
            self.conversation_id,
            call.id,
            call.name,
        )
        task = asyncio.create_task(tool.execute(payload))
        try:
            value = await asyncio.wait_for(task, timeout=timeout)
            result = _tool_result(call.id, value)
        except TimeoutError:
            result = _result(call.id, _with_partial(f"[system] {call.name}工具调用已超过{int(timeout)}s, 被强制中断.", task))
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            _copy_partial(task, asyncio.current_task())
            raise
        except Exception as exc:
            result = _result(call.id, f"{call.name} failed: {_exception_detail(exc)}")
        finally:
            _reset_tool_progress(progress_token)
        self._emit_tool_result_end(call, result)
        return result

    async def _wait_prepared(self, name: str) -> None:
        task = self.prepare_tasks.get(name)
        if task is None:
            return
        await asyncio.shield(task)

    def _emit_tool_result_end(self, call: ToolCall, result: ToolResult) -> None:
        self.emit(
            ConversationStreamPayload(
                self.conversation_id,
                StreamEvent(
                    call.id,
                    "tool_result_end",
                    ToolResultEndPayload(
                        result.tool_call_id,
                        call.name,
                        msgspec.to_builtins(result.content),
                    ),
                ),
            )
        )


def _bind_tool_progress(
    emit: EmitFn,
    conversation_id: str,
    tool_call_id: str,
    tool_name: str,
) -> Token[ToolProgress | None]:
    from ..tools import progress as progress_module

    return progress_module._current_progress.set(
        bind_progress(
            emit,
            conversation_id,
            tool_call_id,
            tool_name,
        )
    )


def _reset_tool_progress(token: Token[ToolProgress | None]) -> None:
    from ..tools import progress as progress_module

    progress_module._current_progress.reset(token)


def _result(tool_call_id: str, text: str) -> ToolResult:
    return _tool_result(tool_call_id, text)


def _tool_result(tool_call_id: str, value: object) -> ToolResult:
    if isinstance(value, list):
        content: list[ContentItem] = []
        for item in value:
            if not isinstance(item, ContentItem):
                continue
            text = redact_value(item.text)
            meta = redact_value(item.meta)
            meta_value = meta if isinstance(meta, dict) else item.meta
            content.append(
                ContentItem(
                    item.kind,
                    text if isinstance(text, str) else item.text,
                    item.path,
                    item.url,
                    item.mime,
                    cast(dict[str, object], meta_value),
                )
            )
        return ToolResult(
            tool_call_id=tool_call_id,
            content=content,
        )
    redacted = redact_value(value)
    text = redacted if isinstance(redacted, str) else str(redacted)
    return ToolResult(tool_call_id=tool_call_id, content=[ContentItem("text", text)])


def _with_partial(text: str, task: asyncio.Task[object]) -> str:
    """Tools may attach ``partial_result`` to their task before cancellation lands."""
    partial = getattr(task, "partial_result", "")
    if not isinstance(partial, str) or not partial:
        return text
    return f"{text}\n该工具产生的临时result为：{redact_value(partial)}"


def _copy_partial(source: asyncio.Task[object], target: asyncio.Task[object] | None) -> None:
    partial = getattr(source, "partial_result", "")
    if target is not None and isinstance(partial, str) and partial:
        setattr(target, "partial_result", partial)


def _exception_detail(exc: BaseException) -> str:
    parts = [_single_exception_detail(exc)]
    cause = exc.__cause__ or exc.__context__
    while cause is not None:
        parts.append(_single_exception_detail(cause))
        cause = cause.__cause__ or cause.__context__
    return "; caused by ".join(parts)


def _single_exception_detail(exc: BaseException) -> str:
    message = str(exc).strip()
    if not message:
        message = repr(exc)
    detail = f"{type(exc).__name__}: {message}"
    redacted = redact_value(detail)
    return redacted if isinstance(redacted, str) else detail
