"""Harness: validates, deserializes, and concurrently executes tool calls.

A Harness lives for exactly one ``run_loop``. Every failure mode — validation
error, execution error, timeout, interrupt — is converted into a ``ToolResult``
handed back to the model; nothing propagates to the conversation.
"""

import asyncio
from typing import cast

import msgspec
from attrs import define

from ..domain.messages import ContentItem, ConversationContext, ToolResult
from ..domain.stream import ToolCall
from ..runtime.core import Runtime
from ..tools import Tool, ToolConfig, build_tools

TOOL_TIMEOUT_S = 240


class HarnessConfig(msgspec.Struct, frozen=True, kw_only=True):
    tools: dict[str, ToolConfig] = msgspec.field(default_factory=dict)


@define
class Harness:
    tools: dict[str, Tool]

    @classmethod
    def from_config(cls, config: HarnessConfig, context: ConversationContext, runtime: Runtime) -> "Harness":
        return cls(tools=build_tools(config.tools, context, runtime))

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
                        results[tasks[task].id] = _result(tasks[task].id, _with_partial("[system] tool call interrupted.", task))
                    break
        finally:
            stop_task.cancel()
        return [results[call.id] for call in tool_calls]

    async def close(self) -> None:
        for tool in self.tools.values():
            await tool.close()

    async def _run_one(self, call: ToolCall, timeout: float) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return _result(call.id, f"unknown tool: {call.name}")
        try:
            raw = msgspec.json.decode((call.arguments or "{}").encode(), type=dict[str, object])
            payload = msgspec.convert(raw, tool.payload_type)
        except msgspec.DecodeError as exc:
            return _result(call.id, f"invalid JSON for {call.name}: {exc}")
        except msgspec.ValidationError as exc:
            return _result(call.id, f"invalid payload for {call.name}: {exc}")
        task = asyncio.create_task(tool.execute(payload))
        try:
            value = await asyncio.wait_for(task, timeout=timeout)
            return ToolResult(tool_call_id=call.id, content=value if isinstance(value, list) else [ContentItem(kind="text", text=value)])
        except TimeoutError:
            return _result(call.id, _with_partial(f"[system] {call.name}工具调用已超过{int(timeout)}s, 被强制中断.", task))
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            _copy_partial(task, asyncio.current_task())
            raise
        except Exception as exc:
            return _result(call.id, f"{call.name} failed: {exc}")


def _result(tool_call_id: str, text: str) -> ToolResult:
    return ToolResult(tool_call_id=tool_call_id, content=[ContentItem(kind="text", text=text)])


def _with_partial(text: str, task: asyncio.Task[object]) -> str:
    """Tools may attach ``partial_result`` to their task before cancellation lands."""
    partial = getattr(task, "partial_result", "")
    if not isinstance(partial, str) or not partial:
        return text
    return f"{text}\n该工具产生的临时result为：{partial}"


def _copy_partial(source: asyncio.Task[object], target: asyncio.Task[object] | None) -> None:
    partial = getattr(source, "partial_result", "")
    if target is not None and isinstance(partial, str) and partial:
        setattr(target, "partial_result", partial)
