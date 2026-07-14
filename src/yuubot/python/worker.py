"""Single ipykernel subprocess managed by the daemon."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import queue
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Callable, Literal, cast

from attrs import define, field
from jupyter_client.asynchronous.client import AsyncKernelClient
from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import AsyncKernelManager

from yuubot.runtime.pty_display import filter_tool_output

from .config import RECYCLE_EXIT_CODE
from .workspace import ensure_workspace_venv, prepare_kernel_workspace

WorkerState = Literal["idle", "leased"]
KERNEL_START_TIMEOUT_S = 30.0
KERNEL_INTERRUPT_GRACE_S = 1.0
KERNEL_SHUTDOWN_TIMEOUT_S = 1.0

_log = logging.getLogger(__name__)


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
    _active_msg_id: str | None = field(default=None, init=False)

    @classmethod
    async def start(
        cls,
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

    async def run_code(self, code: str, on_output: Callable[[str], None] | None = None) -> str:
        if not self.alive:
            raise KernelWorkerError("kernel worker is not alive")
        payload = await self._execute(code, on_output)
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

    async def interrupt_and_shutdown(
        self,
        on_output: Callable[[str], None] | None = None,
        grace_s: float = KERNEL_INTERRUPT_GRACE_S,
    ) -> None:
        """Apply Ctrl+C semantics, collect termination output, then discard the kernel."""
        msg_id = self._active_msg_id
        started = time.monotonic()
        interrupted = False
        if self.alive:
            try:
                interrupted = await _bounded_control(
                    self.manager.interrupt_kernel(), 0.5
                )
            except Exception:
                _log.warning("kernel interrupt signal failed", exc_info=True)
        _log.info(
            "kernel interrupt requested msg_id=%s signalled=%s grace_s=%s",
            msg_id,
            interrupted,
            grace_s,
        )

        deadline = time.monotonic() + grace_s
        while msg_id is not None and self.alive and time.monotonic() < deadline:
            try:
                msg = await self.client.get_iopub_msg(
                    timeout=min(0.1, max(0.0, deadline - time.monotonic()))
                )
            except (asyncio.TimeoutError, queue.Empty):
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            output = _message_output(msg)
            if output and on_output is not None:
                on_output(output)
            if (
                msg.get("header", {}).get("msg_type") == "status"
                and msg.get("content", {}).get("execution_state") == "idle"
            ):
                break

        self.client.stop_channels()
        if self.alive:
            try:
                stopped = await _bounded_control(
                    self.manager.shutdown_kernel(now=True),
                    KERNEL_SHUTDOWN_TIMEOUT_S,
                )
                if not stopped:
                    await self._force_kill()
            except Exception:
                await self._force_kill()
        self._started = False
        _log.info(
            "kernel forced drop completed msg_id=%s duration_ms=%s alive=%s",
            msg_id,
            int((time.monotonic() - started) * 1000),
            self.alive,
        )

    async def _force_kill(self) -> None:
        provisioner = self.manager.provisioner
        kill = getattr(provisioner, "kill", None) if provisioner is not None else None
        if kill is None:
            return
        with contextlib.suppress(Exception):
            result = kill(restart=False)
            if inspect.isawaitable(result):
                await _bounded_control(result, KERNEL_SHUTDOWN_TIMEOUT_S)

    async def _bootstrap(self) -> None:
        await self._execute("import worker_runtime; worker_runtime.bootstrap()")

    async def _execute(self, code: str, on_output: Callable[[str], None] | None = None) -> str:
        msg_id = self.client.execute(code, store_history=False)
        self._active_msg_id = msg_id
        raw_parts: list[str] = []
        truncated = False
        deadline = time.monotonic() + self.execution_timeout_s

        def publish_output(raw: str) -> bool:
            nonlocal truncated
            if not raw:
                return True
            raw_parts.append(raw)
            output = filter_tool_output("".join(raw_parts))
            encoded = output.encode()
            if len(encoded) > self.max_output_bytes:
                truncated = True
                if on_output is not None:
                    on_output(raw)
                return False
            if on_output is not None:
                on_output(raw)
            return True
        try:
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
                output_part = _message_output(msg)
                if output_part and not publish_output(output_part):
                    break
                if (
                    msg.get("header", {}).get("msg_type") == "status"
                    and msg.get("content", {}).get("execution_state") == "idle"
                ):
                    break
            output = filter_tool_output("".join(raw_parts))
            if truncated:
                output = output.encode()[: self.max_output_bytes].decode("utf-8", errors="replace")
                suffix = f"\n[system] output truncated at {self.max_output_bytes} bytes"
                output = f"{output}{suffix}"
                if on_output is not None:
                    on_output(suffix)
            return output
        except asyncio.CancelledError:
            await self.interrupt_and_shutdown(on_output)
            raise
        finally:
            if self._active_msg_id == msg_id:
                self._active_msg_id = None


def _message_output(msg: dict[str, Any]) -> str:
    msg_type = msg.get("header", {}).get("msg_type")
    content = msg.get("content", {})
    if msg_type == "stream":
        return str(content.get("text", ""))
    if msg_type == "execute_result":
        data = content.get("data", {})
        if isinstance(data, dict) and "text/plain" in data:
            return str(data["text/plain"])
    if msg_type == "error":
        return "\n".join(str(line) for line in content.get("traceback", []))
    return ""


async def _bounded_control(awaitable: Awaitable[object], timeout: float) -> bool:
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        await task
        return True
    task.cancel()
    task.add_done_callback(_consume_control_result)
    return False


def _consume_control_result(task: asyncio.Future[object]) -> None:
    try:
        task.result()
    except BaseException:
        pass
