from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yuubot.tools.execute_python import ExecutePythonPayload, ExecutePythonTool


class FakeWorker:
    alive = True

    async def run_code(self, code: str, on_output: object = None) -> str:
        del on_output
        return f"ran:{code}"


class FakePool:
    def __init__(self) -> None:
        self.acquire_calls = 0

    async def acquire(self, workspace: Path, lease_key: str, env: dict[str, str]) -> FakeWorker:
        del workspace, lease_key, env
        self.acquire_calls += 1
        return FakeWorker()

    async def release(self, lease_key: str) -> None:
        del lease_key

    async def drop_leased_worker(self, lease_key: str, worker: FakeWorker) -> None:
        del lease_key, worker


class BlockingWorker:
    alive = True

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()

    async def run_code(self, code: str, on_output: object = None) -> str:
        del code, on_output
        self.calls += 1
        if self.calls == 1:
            return "configured"
        self.started.set()
        await asyncio.Event().wait()
        return "unreachable"


class CancellationPool:
    def __init__(self, worker: BlockingWorker) -> None:
        self.worker = worker
        self.drop_calls = 0
        self.release_calls = 0

    async def acquire(self, workspace: Path, lease_key: str, env: dict[str, str]) -> BlockingWorker:
        del workspace, lease_key, env
        return self.worker

    async def release(self, lease_key: str) -> None:
        del lease_key
        self.release_calls += 1

    async def drop_leased_worker(self, lease_key: str, worker: BlockingWorker) -> None:
        del lease_key
        assert worker is self.worker
        self.drop_calls += 1


@pytest.mark.asyncio
async def test_execute_python_reuses_worker_within_tool_instance(
    tmp_path: Path,
) -> None:
    pool = FakePool()
    tool = ExecutePythonTool(pool=pool, workspace=tmp_path, lease_key="c1", env={})

    first = await tool.execute(ExecutePythonPayload("x = 1"))
    second = await tool.execute(ExecutePythonPayload("print(x)"))

    assert first == "ran:x = 1"
    assert second == "ran:print(x)"
    assert pool.acquire_calls == 1


@pytest.mark.asyncio
async def test_execute_python_cancellation_drops_worker_without_release(
    tmp_path: Path,
) -> None:
    worker = BlockingWorker()
    pool = CancellationPool(worker)
    tool = ExecutePythonTool(pool=pool, workspace=tmp_path, lease_key="c1", env={})
    task = asyncio.create_task(tool.execute(ExecutePythonPayload("while True: pass")))
    await worker.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await tool.close()

    assert pool.drop_calls == 1
    assert pool.release_calls == 0
