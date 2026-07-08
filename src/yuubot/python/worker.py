"""Single ipykernel subprocess managed by the daemon."""

from __future__ import annotations

import asyncio
import contextlib
import queue
import re
import time
from pathlib import Path
from typing import Any, Callable, Literal, cast

from attrs import define, field
from jupyter_client.asynchronous.client import AsyncKernelClient
from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import AsyncKernelManager
from strip_ansi import strip_ansi

from .config import RECYCLE_EXIT_CODE
from .workspace import ensure_workspace_venv, prepare_kernel_workspace

WorkerState = Literal["idle", "leased"]
KERNEL_START_TIMEOUT_S = 30.0
_OSC_CONTROL_RE = re.compile(r"\x1B\][^\x1B\x07]*(?:\x07|\x1B\\)")
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


class KernelWorkerError(RuntimeError):
    pass


@define
class KernelWorker:
    workspace: Path
    env: dict[str, str]
    max_rss_bytes: int
    max_output_bytes: int
    execution_timeout_s: float
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
        max_output_bytes: int,
        execution_timeout_s: float,
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
        manager = AsyncKernelManager(transport_encryption="required")
        manager._kernel_spec = KernelSpec(
            argv=[str(python), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            display_name="yuubot-workspace",
            language="python",
            metadata={"supported_encryption": "curve"},
        )
        client: AsyncKernelClient | None = None
        try:
            await asyncio.wait_for(
                manager.start_kernel(
                    cwd=str(root),
                    env=kernel_env,
                ),
                timeout=KERNEL_START_TIMEOUT_S,
            )
            client = manager.client(
                curve_publickey=manager.curve_publickey,
                curve_secretkey=manager.curve_secretkey,
            )
            client.start_channels()
            await asyncio.wait_for(client.wait_for_ready(), timeout=KERNEL_START_TIMEOUT_S)
            worker = cls(
                workspace=root,
                env=kernel_env,
                max_rss_bytes=max_rss_bytes,
                max_output_bytes=max_output_bytes,
                execution_timeout_s=execution_timeout_s,
                manager=manager,
                client=client,
            )
            await asyncio.wait_for(worker._bootstrap(), timeout=KERNEL_START_TIMEOUT_S)
            worker._started = True
            return worker
        except TimeoutError as exc:
            if client is not None:
                client.stop_channels()
            with contextlib.suppress(Exception):
                await manager.shutdown_kernel(now=True)
            raise KernelWorkerError(f"kernel worker did not become ready within {int(KERNEL_START_TIMEOUT_S)}s") from exc
        except BaseException:
            if client is not None:
                client.stop_channels()
            with contextlib.suppress(Exception):
                await manager.shutdown_kernel(now=True)
            raise

    @property
    def workspace_key(self) -> str:
        return str(self.workspace)

    @property
    def alive(self) -> bool:
        provisioner = self.manager.provisioner
        if provisioner is None or not provisioner.has_process:
            return False
        process = cast(Any, provisioner).process
        return process is not None and process.poll() is None

    @property
    def exit_code(self) -> int | None:
        provisioner = self.manager.provisioner
        if provisioner is None or not provisioner.has_process:
            return None
        process = cast(Any, provisioner).process
        if process is None:
            return None
        return cast(int | None, process.poll())

    def mark_leased(self) -> None:
        self.state = "leased"

    def mark_idle(self) -> None:
        self.state = "idle"
        self.idle_since = time.monotonic()

    async def run_code(self, code: str, *, on_output: Callable[[str], None] | None = None) -> str:
        if not self.alive:
            raise KernelWorkerError("kernel worker is not alive")
        payload = await self._execute(code, on_output=on_output)
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
        await self._execute("import worker_runtime; worker_runtime.bootstrap()")

    async def _execute(self, code: str, *, on_output: Callable[[str], None] | None = None) -> str:
        msg_id = self.client.execute(code, store_history=False)
        chunks: list[str] = []
        truncated = False
        deadline = time.monotonic() + self.execution_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KernelWorkerError(
                    f"kernel execution exceeded {int(self.execution_timeout_s)}s"
                )
            try:
                msg = await self.client.get_iopub_msg(timeout=min(1.0, remaining))
            except (asyncio.TimeoutError, queue.Empty) as exc:
                if not self.alive:
                    raise KernelWorkerError(f"kernel worker exited with code {self.exit_code}") from exc
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            msg_type = msg["header"]["msg_type"]
            content = msg["content"]
            if msg_type == "stream":
                text = _filter_tool_text(str(content.get("text", "")))
                if text:
                    total = sum(len(part.encode()) for part in chunks) + len(text.encode())
                    if total > self.max_output_bytes:
                        truncated = True
                        allowed = self.max_output_bytes - sum(len(part.encode()) for part in chunks)
                        if allowed > 0:
                            text = text.encode()[:allowed].decode("utf-8", errors="replace")
                            chunks.append(text)
                            if on_output is not None:
                                on_output(text)
                        break
                    chunks.append(text)
                    if on_output is not None:
                        on_output(text)
            elif msg_type == "execute_result":
                data = content.get("data", {})
                if isinstance(data, dict) and "text/plain" in data:
                    text = _filter_tool_text(str(data["text/plain"]))
                    total = sum(len(part.encode()) for part in chunks) + len(text.encode())
                    if total > self.max_output_bytes:
                        truncated = True
                        break
                    chunks.append(text)
                    if on_output is not None:
                        on_output(text)
            elif msg_type == "error":
                text = _filter_tool_text("\n".join(str(line) for line in content.get("traceback", [])))
                chunks.append(text)
                if text and on_output is not None:
                    on_output(text)
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break
        output = "".join(chunks)
        if truncated:
            suffix = f"\n[system] output truncated at {self.max_output_bytes} bytes"
            output = f"{output}{suffix}"
            if on_output is not None:
                on_output(suffix)
        return output


def _filter_tool_text(text: str) -> str:
    filtered = strip_ansi(_OSC_CONTROL_RE.sub("", text))
    return _C0_CONTROL_RE.sub("", filtered)
