"""execute_python tool backed by ipykernel worker subprocesses."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar, cast

import msgspec
from attrs import define, field

from ..domain.messages import ConversationContext
from ..python.facade import remove_facade
from ..python.pool import KernelPool, KernelPoolBusy
from ..python.worker import KernelWorker, KernelWorkerError
from ..python.workspace import ensure_workspace_venv, prepare_kernel_workspace
from ..runtime.core import Runtime
from ..runtime.tasks import make_owner
from .base import ToolConfig, ToolSpec

DESCRIPTION = """Run Python code in a persistent IPython session for the current user turn.

The working directory is the actor workspace. Standard output and standard error are captured and returned as text. An empty capture returns `ok`. The session supports native top-level `await`.

Enabled integrations inject credentials into the process environment for `yext` facades, for example `await yext.web.search(query)` and `repo = yext.github.repo(); await repo.issues.list_recent()`.

The session resets after each user turn. Variables, imports, and in-memory side effects do not survive; a developer notice appears when a prior session is gone.

The runtime is headless: `plt.show()` does not reach the user. Save generated files under the workspace, normally `artifacts/`. For images, embed the saved file as a Markdown image in your final response.

After `uv add` or `uv remove`, call `restart_kernel` before expecting new imports.

Runtime facades (`yb.tasks`, `yb.mcps`, cron) and admin-page patterns are documented in the system prompt Integration SDKs and Tool Suggestions."""


class ExecutePythonPayload(msgspec.Struct, frozen=True, kw_only=True):
    code: str


@define
class ExecutePythonTool:
    payload_type: ClassVar[type[msgspec.Struct]] = ExecutePythonPayload

    pool: KernelPool
    workspace: Path
    lease_key: str
    env: dict[str, str]
    _worker: KernelWorker | None = field(default=None, init=False)
    _leased: bool = field(default=False, init=False)

    async def prepare(self) -> None:
        root = self.workspace.resolve()
        prepare_kernel_workspace(root)
        await ensure_workspace_venv(root)

    async def execute(self, payload: msgspec.Struct) -> str:
        data = cast(ExecutePythonPayload, payload)
        self._set_partial_result("execute_python is acquiring a Python kernel worker and preparing the workspace environment.")
        worker = await self._worker_or_acquire()
        try:
            self._set_partial_result("execute_python acquired a Python kernel worker and is executing the submitted code.")
            return await worker.run_code(data.code)
        except KernelWorkerError as first_exc:
            if self._worker is not None:
                await self.pool.drop_leased_worker(self.lease_key, self._worker)
                self._worker = None
                self._leased = False
            self._set_partial_result("execute_python is retrying after the Python kernel worker failed.")
            worker = await self._worker_or_acquire()
            try:
                self._set_partial_result("execute_python acquired a replacement Python kernel worker and is executing the submitted code.")
                return await worker.run_code(data.code)
            except KernelWorkerError as retry_exc:
                raise KernelWorkerError(
                    "kernel worker retry failed after initial error: "
                    f"{first_exc}; retry error: {retry_exc}"
                ) from retry_exc

    async def close(self) -> None:
        if not self._leased:
            return
        await self.pool.release(self.lease_key)
        self._worker = None
        self._leased = False

    async def _worker_or_acquire(self) -> KernelWorker:
        if self._worker is not None and not self._worker.alive:
            await self.pool.drop_leased_worker(self.lease_key, self._worker)
            self._worker = None
            self._leased = False
        if self._worker is not None and self._worker.alive:
            return self._worker
        try:
            self._worker = await self.pool.acquire(self.workspace, lease_key=self.lease_key, env=self.env)
        except KernelPoolBusy as exc:
            raise KernelWorkerError(
                "no python kernel worker available; all workers are busy, try again later"
            ) from exc
        self._leased = True
        return self._worker

    def _set_partial_result(self, text: str) -> None:
        task = asyncio.current_task()
        if task is not None:
            setattr(task, "partial_result", text)


def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> ExecutePythonTool:
    del config
    env = {key: value for integration_env in context.integrations.values() for key, value in integration_env.items()}
    daemon_url = context.rpc.get("daemon_url")
    if isinstance(daemon_url, str) and daemon_url:
        env["YUUBOT_DAEMON_URL"] = daemon_url
    env["YUUBOT_TASK_OWNER"] = make_owner(actor_id=context.actor, conversation_id=context.conversation_id)
    db_path = runtime.db_dir / "yuubot.db"
    env["YUUBOT_DB_PATH"] = str(db_path)
    env["TMPDIR"] = str(runtime.tmp_dir)
    return ExecutePythonTool(
        pool=runtime.actors[context.actor].kernels,
        workspace=context.workspace.resolve(),
        lease_key=context.conversation_id,
        env=env,
    )


async def _uninstall(config: ToolConfig, workspace: Path) -> None:
    del config
    remove_facade(workspace.resolve())


EXECUTE_PYTHON_SPEC = ToolSpec(
    payload_type=ExecutePythonPayload,
    description=DESCRIPTION,
    factory=_factory,
    uninstall=_uninstall,
)
