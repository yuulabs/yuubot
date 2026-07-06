from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from yuubot.python.worker import KernelWorker, KernelWorkerError


class FakeKernelClient:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = messages

    def execute(self, code: str, store_history: bool = False) -> str:
        del code, store_history
        return "m1"

    async def get_iopub_msg(self, timeout: float) -> dict[str, object]:
        del timeout
        if not self._messages:
            raise asyncio.TimeoutError
        return self._messages.pop(0)


def _worker(client: FakeKernelClient, *, max_output_bytes: int = 8, execution_timeout_s: float = 5.0) -> KernelWorker:
    return KernelWorker(
        workspace=Path("/tmp"),
        env={},
        max_rss_bytes=1024,
        max_output_bytes=max_output_bytes,
        execution_timeout_s=execution_timeout_s,
        manager=cast(Any, object()),
        client=cast(Any, client),
    )


@pytest.mark.asyncio
async def test_execute_truncates_output_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeKernelClient(
        [
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "stream"},
                "content": {"text": "0123456789"},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "status"},
                "content": {"execution_state": "idle"},
            },
        ]
    )
    worker = _worker(client)
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))

    output = await worker._execute("print('x')")

    assert "output truncated at 8 bytes" in output


@pytest.mark.asyncio
async def test_execute_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowClient(FakeKernelClient):
        async def get_iopub_msg(self, timeout: float) -> dict[str, object]:
            del timeout
            await asyncio.sleep(0.05)
            return {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "stream"},
                "content": {"text": "x"},
            }

    worker = _worker(SlowClient([]), execution_timeout_s=0.01)
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))

    with pytest.raises(KernelWorkerError, match="exceeded"):
        await worker._execute("while True: pass")
