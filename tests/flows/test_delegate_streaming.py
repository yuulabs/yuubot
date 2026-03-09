"""End-to-end tests for delegate with OutputBuffer streaming."""

from __future__ import annotations

import asyncio

import pytest

from yuuagents.running_tools import OutputBuffer, RunningToolRegistry


@pytest.mark.asyncio
async def test_output_buffer_accumulates_data():
    """验证 OutputBuffer 能正确累积数据并提供 tail/full 访问。"""
    buffer = OutputBuffer()

    buffer.write(b"Line 1\n")
    buffer.write(b"Line 2\n")
    buffer.write(b"Line 3")

    full = buffer.full()
    tail = buffer.tail(n_bytes=10)

    assert "Line 1" in full
    assert "Line 2" in full
    assert "Line 3" in full
    assert len(tail) <= 10


@pytest.mark.asyncio
async def test_output_buffer_streaming_simulation():
    """模拟流式写入场景，验证父 agent 能实时读取进展。"""
    buffer = OutputBuffer()

    async def simulate_progress(buf: OutputBuffer):
        for i in range(5):
            buf.write(f"Step {i + 1}/5 complete\n".encode())
            await asyncio.sleep(0.01)

    task = asyncio.create_task(simulate_progress(buffer))

    await asyncio.sleep(0.03)
    mid_content = buffer.full()
    assert "Step 1" in mid_content

    await task
    final_content = buffer.full()
    assert "Step 5" in final_content


@pytest.mark.asyncio
async def test_running_tool_registry_registration():
    """验证 RunningToolRegistry 能正确注册和检查任务。"""
    registry = RunningToolRegistry()
    buffer = OutputBuffer()

    async def slow_task():
        await asyncio.sleep(10)
        from yuutrace.context import ToolResult

        return ToolResult(tool_call_id="tc1", output="Done")

    task = asyncio.create_task(slow_task())
    handle = registry.register("delegate", task, buffer, "tc1")

    assert len(handle) == 8
    assert handle in registry._entries

    result = await registry.check(handle, wait=0.05)
    assert "Still running" in result

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_running_tool_registry_cancel():
    """验证能通过 registry 取消运行中的任务。"""
    registry = RunningToolRegistry()
    buffer = OutputBuffer()

    async def infinite_task():
        while True:
            await asyncio.sleep(1)

    task = asyncio.create_task(infinite_task())
    handle = registry.register("delegate", task, buffer, "tc2")

    msg = registry.cancel(handle)
    assert "Cancelled" in msg

    await asyncio.sleep(0.01)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_delegate_receives_output_buffer_via_context():
    """验证 delegate 工具能通过 context 获取 output_buffer。"""
    from yuuagents.context import AgentContext
    from yuuagents.running_tools import OutputBuffer

    buf = OutputBuffer()
    ctx = AgentContext(
        task_id="a1b2c3d4e5f6789012345678abcdef01",
        agent_id="test-agent",
        workdir="/tmp",
        docker_container="",
        current_output_buffer=buf,
    )

    assert ctx.current_output_buffer is buf

    ctx.current_output_buffer.write(b"Test data")
    assert "Test data" in buf.full()
