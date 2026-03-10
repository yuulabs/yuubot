"""Tests for soft timeout behavior with long-running tool calls."""

from __future__ import annotations

import asyncio

import pytest

from yuuagents.running_tools import OutputBuffer, RunningToolRegistry
from yuutrace.context import ToolResult, ToolsContext
from opentelemetry import trace


@pytest.mark.asyncio
async def test_soft_timeout_triggers_for_long_running_tool():
    """当工具执行时间超过 soft_timeout 时，应返回占位符并注册到 registry."""
    tracer = trace.get_tracer("test")
    registry = RunningToolRegistry()

    async def slow_tool() -> str:
        await asyncio.sleep(10)
        return "completed after long wait"

    with tracer.start_as_current_span("test") as span:
        ctx = ToolsContext(span, tracer)
        results = await ctx.gather(
            [{"tool_call_id": "tc1", "name": "slow_tool", "tool": slow_tool}],
            soft_timeout=0.3,
            registry=registry,
        )

    assert len(results) == 1
    result = results[0]
    assert result.tool_call_id == "tc1"
    assert "still running" in str(result.output).lower() or "handle=" in str(
        result.output
    )
    assert len(registry._entries) == 1

    for entry in list(registry._entries.values()):
        entry.task.cancel()
        try:
            await entry.task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_soft_timeout_does_not_trigger_for_fast_tool():
    """当工具执行时间小于 soft_timeout 时，应正常返回结果."""
    tracer = trace.get_tracer("test")
    registry = RunningToolRegistry()

    async def fast_tool() -> str:
        await asyncio.sleep(0.05)
        return "fast result"

    with tracer.start_as_current_span("test") as span:
        ctx = ToolsContext(span, tracer)
        results = await ctx.gather(
            [{"tool_call_id": "tc2", "name": "fast_tool", "tool": fast_tool}],
            soft_timeout=0.5,
            registry=registry,
        )

    assert len(results) == 1
    assert results[0].output == "fast result"
    assert len(registry._entries) == 0


@pytest.mark.asyncio
async def test_soft_timeout_with_multiple_tools():
    """多个工具同时调用时，部分超时部分正常完成."""
    tracer = trace.get_tracer("test")
    registry = RunningToolRegistry()

    async def slow_tool() -> str:
        await asyncio.sleep(10)
        return "slow result"

    async def medium_tool() -> str:
        await asyncio.sleep(0.1)
        return "medium result"

    async def fast_tool() -> str:
        return "fast result"

    with tracer.start_as_current_span("test") as span:
        ctx = ToolsContext(span, tracer)
        results = await ctx.gather(
            [
                {"tool_call_id": "slow", "name": "slow_tool", "tool": slow_tool},
                {"tool_call_id": "medium", "name": "medium_tool", "tool": medium_tool},
                {"tool_call_id": "fast", "name": "fast_tool", "tool": fast_tool},
            ],
            soft_timeout=0.2,
            registry=registry,
        )

    results_map = {r.tool_call_id: r for r in results}
    assert results_map["fast"].output == "fast result"
    assert results_map["medium"].output == "medium result"
    assert "still running" in str(results_map["slow"].output).lower()
    assert len(registry._entries) == 1

    for entry in list(registry._entries.values()):
        entry.task.cancel()
        try:
            await entry.task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_poll_returns_immediately_when_tool_done():
    """轮询已完成的工具应立即返回结果."""
    registry = RunningToolRegistry()
    buf = OutputBuffer()

    async def quick_tool() -> ToolResult:
        return ToolResult(tool_call_id="tc_quick", output="done!")

    task = asyncio.create_task(quick_tool())
    await task
    handle = registry.register("quick_tool", task, buf, "tc_quick")

    start = asyncio.get_event_loop().time()
    result = await registry.check(handle, wait=5)
    elapsed = asyncio.get_event_loop().time() - start

    assert "done!" in result
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_poll_blocks_until_completion():
    """轮询运行中的工具应阻塞直到完成或等待超时."""
    registry = RunningToolRegistry()
    buf = OutputBuffer()

    async def slow_tool() -> ToolResult:
        await asyncio.sleep(0.3)
        return ToolResult(tool_call_id="tc_slow", output="finished!")

    task = asyncio.create_task(slow_tool())
    handle = registry.register("slow_tool", task, buf, "tc_slow")

    start = asyncio.get_event_loop().time()
    result = await registry.check(handle, wait=5)
    elapsed = asyncio.get_event_loop().time() - start

    assert "finished!" in result
    assert 0.25 < elapsed < 0.5


@pytest.mark.asyncio
async def test_poll_returns_tail_output_on_wait_timeout():
    """轮询超时时应返回当前 tail output."""
    registry = RunningToolRegistry()
    buf = OutputBuffer()
    buf.write(b"Line 1: Initial output\n")
    buf.write(b"Line 2: More progress\n")
    buf.write(b"Line 3: Current work...\n")

    async def very_slow_tool() -> ToolResult:
        await asyncio.sleep(100)
        return ToolResult(tool_call_id="tc_vslow", output="never")

    task = asyncio.create_task(very_slow_tool())
    handle = registry.register("very_slow", task, buf, "tc_vslow")

    result = await registry.check(handle, wait=0.2)

    assert "still running" in result.lower()
    assert "Line 3" in result or "Current work" in result

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_poll_multiple_times_shows_progress():
    """多次轮询应能跟踪工具进度."""
    registry = RunningToolRegistry()
    buf = OutputBuffer()

    async def incremental_tool() -> ToolResult:
        for i in range(5):
            buf.write(f"Step {i + 1} complete\n".encode())
            await asyncio.sleep(0.15)
        return ToolResult(tool_call_id="tc_inc", output="all done")

    task = asyncio.create_task(incremental_tool())
    handle = registry.register("incremental", task, buf, "tc_inc")

    await asyncio.sleep(0.1)
    result1 = await registry.check(handle, wait=0.05)
    assert "still running" in result1.lower()

    result2 = await registry.check(handle, wait=0.05)
    assert "still running" in result2.lower() or "all done" in result2 or "Step" in result2

    result3 = await registry.check(handle, wait=2)
    assert "all done" in result3


@pytest.mark.asyncio
async def test_poll_unknown_handle_returns_error():
    """轮询不存在的 handle 应返回错误."""
    registry = RunningToolRegistry()
    result = await registry.check("unknown_handle_123", wait=1)
    assert "[ERROR]" in result
    assert "unknown" in result.lower()


@pytest.mark.asyncio
async def test_full_soft_timeout_polling_flow():
    """完整流程：工具触发软超时 -> 轮询检查 -> 获取最终结果."""
    tracer = trace.get_tracer("test")
    registry = RunningToolRegistry()

    async def long_task() -> str:
        await asyncio.sleep(0.8)
        return "long task completed"

    with tracer.start_as_current_span("test") as span:
        ctx = ToolsContext(span, tracer)
        results = await ctx.gather(
            [{"tool_call_id": "long", "name": "long_task", "tool": long_task}],
            soft_timeout=0.2,
            registry=registry,
        )

    assert len(results) == 1
    placeholder = results[0]
    assert "still running" in str(placeholder.output).lower()

    output_str = str(placeholder.output)
    handle_start = output_str.find("handle=")
    assert handle_start != -1
    handle = output_str[handle_start + 7 :].split()[0].strip()

    poll_result = await registry.check(handle, wait=2)
    assert "long task completed" in poll_result


@pytest.mark.asyncio
async def test_soft_timeout_then_check_running_tool():
    """Delegate 超时 → placeholder 含 handle → check_running_tool 拿到最终结果."""
    tracer = trace.get_tracer("test")
    registry = RunningToolRegistry()

    async def delegate_like_task() -> str:
        await asyncio.sleep(0.6)
        return "delegate finished successfully"

    # 1) gather with soft_timeout — task exceeds timeout → placeholder
    with tracer.start_as_current_span("test") as span:
        ctx = ToolsContext(span, tracer)
        results = await ctx.gather(
            [
                {
                    "tool_call_id": "del1",
                    "name": "delegate",
                    "tool": delegate_like_task,
                }
            ],
            soft_timeout=0.1,
            registry=registry,
        )

    assert len(results) == 1
    placeholder = str(results[0].output)
    assert "still running" in placeholder.lower()
    assert "handle=" in placeholder

    # 2) Extract handle from placeholder
    handle = placeholder.split("handle=")[1].split()[0].strip()
    assert len(handle) == 8

    # 3) check via registry (same as check_running_tool tool) — should block and return final result
    final = await registry.check(handle, wait=5)
    assert "delegate finished successfully" in final

    # 4) Registry entry cleaned up after retrieval
    assert len(registry._entries) == 0


@pytest.mark.asyncio
async def test_cancel_running_tool_via_registry():
    """通过 registry 取消运行中的工具."""
    registry = RunningToolRegistry()
    buf = OutputBuffer()

    async def infinite_task() -> ToolResult:
        while True:
            await asyncio.sleep(1)

    task = asyncio.create_task(infinite_task())
    handle = registry.register("infinite", task, buf, "tc_inf")

    msg = registry.cancel(handle)
    assert "Cancelled" in msg

    await asyncio.sleep(0.05)
    assert task.cancelled()

    result = await registry.check(handle, wait=0.1)
    assert "cancelled" in result.lower() or "[ERROR]" in result
