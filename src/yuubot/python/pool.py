"""Actor-scoped ipykernel worker pool with a process-wide worker limiter."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path

from attrs import define, field

from .config import PythonKernelsConfig
from .worker import KernelWorker, KernelWorkerError


class KernelPoolBusy(RuntimeError):
    pass


@define
class KernelLimiter:
    """Limits live kernel subprocesses across the daemon."""

    config: PythonKernelsConfig
    _slots: asyncio.Semaphore = field(init=False)

    def __attrs_post_init__(self) -> None:
        self._slots = asyncio.Semaphore(self.config.max_workers)

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.config.acquire_timeout_s)
        except asyncio.TimeoutError as exc:
            raise KernelPoolBusy(
                f"no python kernel worker available within {int(self.config.acquire_timeout_s)}s"
            ) from exc

    def release(self) -> None:
        self._slots.release()


@define
class KernelPool:
    """Keeps warm workers for one actor; leases are per conversation turn."""

    config: PythonKernelsConfig
    limiter: KernelLimiter
    _workers: list[KernelWorker] = field(factory=list)
    _leases: dict[str, KernelWorker] = field(factory=dict)
    _lease_locks: dict[str, asyncio.Lock] = field(factory=lambda: defaultdict(asyncio.Lock))
    _lock: asyncio.Lock = field(factory=asyncio.Lock)
    _idle_task: asyncio.Task[None] | None = field(default=None, init=False)

    def start(self) -> None:
        if self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def shutdown(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
            self._idle_task = None
        async with self._lock:
            workers = list(self._workers)
            self._workers.clear()
            self._leases.clear()
        for worker in workers:
            await worker.shutdown()
            self.limiter.release()

    async def acquire(
        self,
        workspace: Path,
        *,
        lease_key: str,
        env: dict[str, str],
    ) -> KernelWorker:
        async with self._lease_locks[lease_key]:
            async with self._lock:
                leased = self._leases.get(lease_key)
                if leased is not None and leased.alive:
                    return leased

                worker = self._find_idle_worker()
                if worker is not None:
                    worker.mark_leased()
                    self._leases[lease_key] = worker
                    return worker

            await self.limiter.acquire()
            spawned: KernelWorker | None = None
            leased_to_caller = False
            try:
                spawned = await self._spawn_worker(workspace, env=env)
                async with self._lock:
                    leased = self._leases.get(lease_key)
                    if leased is not None and leased.alive:
                        await self._drop_worker_unlocked(spawned)
                        spawned = None
                        return leased
                    spawned.mark_leased()
                    self._leases[lease_key] = spawned
                    leased_to_caller = True
                    return spawned
            except BaseException:
                if spawned is None:
                    self.limiter.release()
                elif not leased_to_caller:
                    async with self._lock:
                        await self._drop_worker_unlocked(spawned)
                raise

    async def release(self, lease_key: str) -> None:
        async with self._lease_locks[lease_key]:
            async with self._lock:
                worker = self._leases.pop(lease_key, None)
            if worker is None:
                return
            if worker.alive:
                try:
                    await worker.reset_or_recycle()
                except KernelWorkerError:
                    async with self._lock:
                        await self._drop_worker_unlocked(worker)
                    return
            async with self._lock:
                worker.mark_idle()
                if not worker.alive:
                    await self._drop_worker_unlocked(worker)

    async def purge_for_restart(self, lease_key: str) -> None:
        async with self._lease_locks[lease_key]:
            victims: list[KernelWorker] = []
            async with self._lock:
                leased = self._leases.pop(lease_key, None)
                if leased is not None:
                    victims.append(leased)
                victims.extend(worker for worker in self._workers if worker.state == "idle")
                self._workers = [worker for worker in self._workers if worker not in victims]
            for worker in victims:
                await worker.shutdown()
                self.limiter.release()

    async def drop_leased_worker(self, lease_key: str, worker: KernelWorker) -> None:
        async with self._lease_locks[lease_key]:
            async with self._lock:
                current = self._leases.get(lease_key)
                if current is worker:
                    self._leases.pop(lease_key, None)
                await self._drop_worker_unlocked(worker)

    def _find_idle_worker(self) -> KernelWorker | None:
        for worker in self._workers:
            if worker.state == "idle" and worker.alive:
                return worker
        return None

    async def _spawn_worker(self, workspace: Path, *, env: dict[str, str]) -> KernelWorker:
        worker = await KernelWorker.start(
            workspace=workspace,
            env=env,
            max_rss_bytes=self.config.max_rss_bytes,
            max_output_bytes=self.config.max_output_bytes,
            execution_timeout_s=self.config.execution_timeout_s,
        )
        self._workers.append(worker)
        return worker

    async def _drop_worker_unlocked(self, worker: KernelWorker) -> None:
        self._leases = {key: value for key, value in self._leases.items() if value is not worker}
        removed = False
        if worker in self._workers:
            self._workers.remove(worker)
            removed = True
        await worker.shutdown()
        if removed:
            self.limiter.release()

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            expired: list[KernelWorker] = []
            async with self._lock:
                for worker in self._workers:
                    if worker.state != "idle":
                        continue
                    if now - worker.idle_since >= self.config.idle_ttl_s:
                        expired.append(worker)
                for worker in expired:
                    self._workers.remove(worker)
                    self._leases = {key: value for key, value in self._leases.items() if value is not worker}
            for worker in expired:
                await worker.shutdown()
                self.limiter.release()
