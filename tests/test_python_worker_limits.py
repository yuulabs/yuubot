from __future__ import annotations

import asyncio
import queue
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


def _worker(client: FakeKernelClient, max_output_bytes: int = 8, execution_timeout_s: float = 5.0) -> KernelWorker:
    return KernelWorker(
        Path("/tmp"),
        {},
        1024,
        max_output_bytes,
        execution_timeout_s,
        cast(Any, object()),
        cast(Any, client),
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


@pytest.mark.asyncio
async def test_execute_treats_queue_empty_as_poll_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyThenDoneClient(FakeKernelClient):
        def __init__(self) -> None:
            super().__init__(
                [
                    {
                        "parent_header": {"msg_id": "m1"},
                        "header": {"msg_type": "status"},
                        "content": {"execution_state": "idle"},
                    },
                ]
            )
            self._empty_once = True

        async def get_iopub_msg(self, timeout: float) -> dict[str, object]:
            if self._empty_once:
                self._empty_once = False
                raise queue.Empty
            if not self._messages:
                raise asyncio.TimeoutError
            return self._messages.pop(0)

    worker = _worker(EmptyThenDoneClient())
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))

    output = await worker.run_code("1 + 1")

    assert output == "ok"


@pytest.mark.asyncio
async def test_execute_filters_terminal_control_sequences(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeKernelClient(
        [
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "stream"},
                "content": {"text": "\x1b[31mred\x1b[0m\n"},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "execute_result"},
                "content": {"data": {"text/plain": "\x1b]0;ignored\x07value"}},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "error"},
                "content": {"traceback": ["\x1b[36mTraceback\x1b[0m", "boom\x07"]},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "status"},
                "content": {"execution_state": "idle"},
            },
        ]
    )
    worker = _worker(client, 128)
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))

    output = await worker._execute("print('x')")

    assert output == "red\nvalueTraceback\nboom"
    assert "\x1b" not in output
    assert "\x07" not in output


@pytest.mark.asyncio
async def test_execute_streams_filtered_output_to_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeKernelClient(
        [
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "stream"},
                "content": {"text": "\x1b[31mhello\x1b[0m\n"},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "execute_result"},
                "content": {"data": {"text/plain": "42"}},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "status"},
                "content": {"execution_state": "idle"},
            },
        ]
    )
    worker = _worker(client, 128)
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))
    streamed: list[str] = []

    output = await worker.run_code("print('hello')", on_output=streamed.append)

    assert streamed == ["hello\n", "42"]
    assert output == "hello\n42"
