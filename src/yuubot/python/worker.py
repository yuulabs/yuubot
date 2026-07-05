"""Single ipykernel subprocess managed by the daemon."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Literal

from attrs import define, field
from jupyter_client import AsyncKernelClient, AsyncKernelManager
from jupyter_client.kernelspec import KernelSpec

from .config import RECYCLE_EXIT_CODE
from .workspace import ensure_workspace_venv, prepare_kernel_workspace

WorkerState = Literal["idle", "leased"]


class KernelWorkerError(RuntimeError):
    pass


@define
class KernelWorker:
    workspace: Path
    env: dict[str, str]
    max_rss_bytes: int
    manager: AsyncKernelManager
    client: AsyncKernelClient
    state: WorkerState = "idle"
    idle_since: float = field(factory=time.monotonic)
    _started: bool = field(default=False, init=False)

    @classmethod
    async def start(
        cls,
        *,
        workspace: Path,
        env: dict[str, str],
        max_rss_bytes: int,
    ) -> KernelWorker:
        root = workspace.resolve()
        prepare_kernel_workspace(root)
        python = await ensure_workspace_venv(root)
        kernel_env = dict(env)
        kernel_env["YUUBOT_WORKER_MAX_RSS_BYTES"] = str(max_rss_bytes)
        yuubot_dir = str(root / ".yuubot")
        facade_dir = str(root / ".yuubot" / "facade")
        existing_path = kernel_env.get("PYTHONPATH", "")
        kernel_env["PYTHONPATH"] = f"{yuubot_dir}:{facade_dir}:{existing_path}" if existing_path else f"{yuubot_dir}:{facade_dir}"
        manager = AsyncKernelManager()
        manager._kernel_spec = KernelSpec(
            argv=[str(python), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            display_name="yuubot-workspace",
            language="python",
        )
        await manager.start_kernel(
            cwd=str(root),
            env=kernel_env,
        )
        client = manager.client()
        client.start_channels()
        await client.wait_for_ready()
        worker = cls(
            workspace=root,
            env=kernel_env,
            max_rss_bytes=max_rss_bytes,
            manager=manager,
            client=client,
        )
        await worker._bootstrap()
        worker._started = True
        return worker

    @property
    def workspace_key(self) -> str:
        return str(self.workspace)

    @property
    def alive(self) -> bool:
        provisioner = self.manager.provisioner
        if provisioner is None or not provisioner.has_process:
            return False
        process = provisioner.process
        return process is not None and process.poll() is None

    @property
    def exit_code(self) -> int | None:
        provisioner = self.manager.provisioner
        if provisioner is None or not provisioner.has_process:
            return None
        process = provisioner.process
        if process is None:
            return None
        return process.poll()

    def mark_leased(self) -> None:
        self.state = "leased"

    def mark_idle(self) -> None:
        self.state = "idle"
        self.idle_since = time.monotonic()

    async def run_code(self, code: str) -> str:
        if not self.alive:
            raise KernelWorkerError("kernel worker is not alive")
        payload = await self._execute(code)
        return payload if payload else "ok"

    async def reset_or_recycle(self) -> None:
        if not self.alive:
            return
        try:
            await self._execute("import worker_runtime; worker_runtime.reset_or_recycle()")
        except KernelWorkerError:
            if not self.alive and self.exit_code == RECYCLE_EXIT_CODE:
                return
            raise
        if not self.alive and self.exit_code == RECYCLE_EXIT_CODE:
            return

    async def shutdown(self) -> None:
        if not self._started:
            return
        self.client.stop_channels()
        if self.alive:
            await self.manager.shutdown_kernel(now=True)
        self._started = False

    async def _bootstrap(self) -> None:
        await self._execute("import worker_runtime")

    async def _execute(self, code: str) -> str:
        msg_id = self.client.execute(code, store_history=False)
        chunks: list[str] = []
        while True:
            try:
                msg = await self.client.get_iopub_msg(timeout=1)
            except asyncio.TimeoutError as exc:
                if not self.alive:
                    raise KernelWorkerError(f"kernel worker exited with code {self.exit_code}") from exc
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            msg_type = msg["header"]["msg_type"]
            content = msg["content"]
            if msg_type == "stream":
                chunks.append(str(content.get("text", "")))
            elif msg_type == "execute_result":
                data = content.get("data", {})
                if isinstance(data, dict) and "text/plain" in data:
                    chunks.append(str(data["text/plain"]))
            elif msg_type == "error":
                chunks.append("\n".join(str(line) for line in content.get("traceback", [])))
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break
        return "".join(chunks)
