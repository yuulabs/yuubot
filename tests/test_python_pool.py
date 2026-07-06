from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from yuubot.python.config import PythonKernelsConfig
from yuubot.python.pool import KernelLimiter, KernelPool
from yuubot.python.worker import KernelWorker


async def test_pool_acquire_releases_slot_when_cancelled_during_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = KernelLimiter(PythonKernelsConfig(max_workers=1, acquire_timeout_s=0.05))
    pool = KernelPool(PythonKernelsConfig(max_workers=1, acquire_timeout_s=0.05), limiter)
    started = asyncio.Event()

    async def wait_forever(
        cls: type[KernelWorker],
        *,
        workspace: Path,
        env: dict[str, str],
        max_rss_bytes: int,
        max_output_bytes: int,
        execution_timeout_s: float,
    ) -> KernelWorker:
        del cls, workspace, env, max_rss_bytes, max_output_bytes, execution_timeout_s
        started.set()
        await asyncio.Future[None]()
        raise AssertionError("unreachable")

    monkeypatch.setattr(KernelWorker, "start", classmethod(wait_forever))

    task = asyncio.create_task(pool.acquire(tmp_path, lease_key="c1", env={}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await limiter.acquire()
    limiter.release()


async def test_pool_acquire_drops_spawned_worker_when_cancelled_before_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = KernelLimiter(PythonKernelsConfig(max_workers=1, acquire_timeout_s=0.05))
    pool = KernelPool(PythonKernelsConfig(max_workers=1, acquire_timeout_s=0.05), limiter)
    shutdown_called = False
    entered_second_lock = asyncio.Event()
    release_second_lock = asyncio.Event()

    class FakeWorker:
        state = "idle"
        alive = True
        idle_since = 0.0

        def mark_leased(self) -> None:
            self.state = "leased"

        async def shutdown(self) -> None:
            nonlocal shutdown_called
            shutdown_called = True

    class GateLock:
        count = 0

        async def __aenter__(self) -> GateLock:
            self.count += 1
            if self.count == 2:
                entered_second_lock.set()
                await release_second_lock.wait()
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    worker = FakeWorker()

    async def spawn_worker(self: KernelPool, workspace: Path, *, env: dict[str, str]) -> FakeWorker:
        del workspace, env
        self._workers.append(cast(KernelWorker, worker))  # noqa: SLF001
        return worker

    monkeypatch.setattr(KernelPool, "_spawn_worker", spawn_worker)
    object.__setattr__(pool, "_lock", GateLock())
    task = asyncio.create_task(pool.acquire(tmp_path, lease_key="c1", env={}))
    await entered_second_lock.wait()
    task.cancel()
    release_second_lock.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert shutdown_called
    await limiter.acquire()
    limiter.release()
