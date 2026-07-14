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

    def stop_channels(self) -> None:
        return None


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
async def test_execute_streams_raw_carriage_return_output(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeKernelClient(
        [
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "stream"},
                "content": {"text": "\r10%"},
            },
            {
                "parent_header": {"msg_id": "m1"},
                "header": {"msg_type": "stream"},
                "content": {"text": "\r80%\n"},
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

    output = await worker.run_code("for i in range(2): print(i)", on_output=streamed.append)

    assert streamed == ["\r10%", "\r80%\n"]
    assert output == "80%\n"


@pytest.mark.asyncio
async def test_execute_streams_raw_output_to_terminal_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert streamed == ["\x1b[31mhello\x1b[0m\n", "42"]
    assert output == "hello\n42"


@pytest.mark.asyncio
async def test_cancelled_execute_interrupts_kernel_and_collects_termination_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptClient(FakeKernelClient):
        def __init__(self) -> None:
            super().__init__(
                [
                    {
                        "parent_header": {"msg_id": "m1"},
                        "header": {"msg_type": "error"},
                        "content": {"traceback": ["KeyboardInterrupt"]},
                    },
                    {
                        "parent_header": {"msg_id": "m1"},
                        "header": {"msg_type": "status"},
                        "content": {"execution_state": "idle"},
                    },
                ]
            )
            self.started = asyncio.Event()
            self.blocking = True

        async def get_iopub_msg(self, timeout: float) -> dict[str, object]:
            del timeout
            if self.blocking:
                self.blocking = False
                self.started.set()
                await asyncio.Event().wait()
            return await super().get_iopub_msg(0)

    class InterruptManager:
        def __init__(self) -> None:
            self.interrupted = False
            self.shutdown = False

        async def interrupt_kernel(self) -> None:
            self.interrupted = True

        async def shutdown_kernel(self, now: bool) -> None:
            assert now is True
            self.shutdown = True

    client = InterruptClient()
    manager = InterruptManager()
    worker = _worker(client, 128)
    worker.manager = cast(Any, manager)
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))
    streamed: list[str] = []
    task = asyncio.create_task(worker.run_code("while True: pass", streamed.append))
    await client.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)

    assert manager.interrupted is True
    assert manager.shutdown is True
    assert streamed == ["KeyboardInterrupt"]


@pytest.mark.asyncio
async def test_unresponsive_kernel_control_is_force_killed_within_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provisioner:
        def __init__(self) -> None:
            self.killed = False

        async def kill(self, restart: bool) -> None:
            assert restart is False
            self.killed = True

    class UnresponsiveManager:
        def __init__(self) -> None:
            self.provisioner = Provisioner()

        async def interrupt_kernel(self) -> None:
            await asyncio.Event().wait()

        async def shutdown_kernel(self, now: bool) -> None:
            assert now is True
            await asyncio.Event().wait()

    manager = UnresponsiveManager()
    worker = _worker(FakeKernelClient([]), 128)
    worker.manager = cast(Any, manager)
    worker._started = True
    monkeypatch.setattr(KernelWorker, "alive", property(lambda self: True))

    started = asyncio.get_running_loop().time()
    await worker.interrupt_and_shutdown(grace_s=0)

    assert asyncio.get_running_loop().time() - started < 2
    assert manager.provisioner.killed is True
