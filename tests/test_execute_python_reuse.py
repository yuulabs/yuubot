from __future__ import annotations

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


@pytest.mark.asyncio
async def test_execute_python_reuses_worker_within_tool_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()
    tool = ExecutePythonTool(pool=pool, workspace=tmp_path, lease_key="c1", env={})
    partial_results: list[str] = []

    def capture_partial(self: ExecutePythonTool, text: str) -> None:
        del self
        partial_results.append(text)

    monkeypatch.setattr(ExecutePythonTool, "_set_partial_result", capture_partial)

    first = await tool.execute(ExecutePythonPayload("x = 1"))
    second = await tool.execute(ExecutePythonPayload("print(x)"))

    assert first == "ran:x = 1"
    assert second == "ran:print(x)"
    assert pool.acquire_calls == 1
    assert partial_results == [
        "execute_python is acquiring a Python kernel worker.",
        "execute_python acquired a Python kernel worker and is executing the submitted code.",
        "execute_python is executing in the existing Python kernel session.",
    ]
