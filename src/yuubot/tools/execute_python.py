"""execute_python tool backed by ipykernel worker subprocesses."""

from __future__ import annotations

import asyncio
import logging
import time
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
from .progress import current_progress

_log = logging.getLogger(__name__)

DESCRIPTION = """Run Python code in a persistent IPython session for the current user turn.

The working directory is the actor workspace. Standard output and standard error are captured and returned as text. An empty capture returns `ok`. The session supports native top-level `await`.

Output is capped at 1 MiB per call. When this limit is reached, the returned text ends with an explicit truncation notice; write large results to workspace files and inspect them with `read` in pages instead of printing them all.

Enabled integrations inject credentials into the process environment for `yext` facades, for example `await yext.web.search(query)` and `repo = yext.github.repo(); await repo.issues.list_recent()`. For Codex, use `import yext.codex as codex`, call `await codex.models()` to discover configured models, then consume `async for event in codex.open_session(...).ask(prompt)` to completion. `open_session()` is lazy and its `session.id` remains `None` until the first `thread.started` event; use only profile names configured in Codex.

The session resets after each user turn. Variables, imports, and in-memory side effects do not survive; a developer notice appears when a prior session is gone.

The runtime is headless: `plt.show()` does not reach the user. Save generated files under the workspace, normally `artifacts/`. For images, embed them in your final response as Markdown, e.g. `![plot](artifacts/plot.png)` or an external `https://...` URL — not `[[...]]`.

After `uv add` or `uv remove`, call `restart_kernel` before expecting new imports.

Runtime facades (`yb.fixer`, `yb.conversations`, `yb.tasks`, `yb.mcps`, cron) and admin-page patterns are documented in the system prompt Integration SDKs and Tool Suggestions. `yb.fixer.ask_gemini` and `ask_grok` each allow one provider-completed request per user turn and return any citations supplied by the provider; include related subquestions in one prompt. `yb.conversations.list_recents()` reads your own recent user-visible conversation history for preference/context recall. `yext.web.search` provides three successful searches per turn, while `read` and `download` remain available for inspecting sources."""


class ExecutePythonPayload(msgspec.Struct, frozen=True):
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
        progress = current_progress()
        if progress is not None:
            progress.set_task("Preparing Python workspace")
        root = self.workspace.resolve()
        prepare_kernel_workspace(root)
        await ensure_workspace_venv(root)

    async def execute(self, payload: msgspec.Struct) -> str:
        data = cast(ExecutePythonPayload, payload)
        worker, newly_acquired = await self._worker_or_acquire()
        try:
            turn_token = self.env.get("YUUBOT_TURN_TOKEN", "")
            await worker.run_code(
                "import yb._turn_guard as _yuubot_turn_guard; "
                f"_yuubot_turn_guard.configure({turn_token!r})"
            )
            progress = current_progress()
            if progress is not None:
                progress.set_task(
                    "Running Python in a new kernel"
                    if newly_acquired
                    else "Running Python"
                )
            try:
                return await worker.run_code(data.code, on_output=progress.write if progress is not None else None)
            except KernelWorkerError as first_exc:
                if self._worker is not None:
                    await self.pool.drop_leased_worker(self.lease_key, self._worker)
                    self._worker = None
                    self._leased = False
                if progress is not None:
                    progress.set_task("Retrying with a replacement Python kernel")
                worker, _ = await self._worker_or_acquire()
                try:
                    return await worker.run_code(
                        data.code,
                        on_output=progress.write if progress is not None else None,
                    )
                except KernelWorkerError as retry_exc:
                    raise KernelWorkerError(
                        "kernel worker retry failed after initial error: "
                        f"{first_exc}; retry error: {retry_exc}"
                    ) from retry_exc
        except asyncio.CancelledError:
            started = time.monotonic()
            progress = current_progress()
            leased = self._worker
            self._worker = None
            self._leased = False
            _log.info(
                "execute_python kernel cancellation started conversation_id=%s tool_call_id=%s tool_name=execute_python",
                self.lease_key,
                progress.tool_call_id if progress is not None else "",
            )
            try:
                if leased is not None:
                    await self.pool.drop_leased_worker(self.lease_key, leased)
            finally:
                _log.info(
                    "execute_python kernel cancellation completed conversation_id=%s tool_call_id=%s tool_name=execute_python duration_ms=%s",
                    self.lease_key,
                    progress.tool_call_id if progress is not None else "",
                    int((time.monotonic() - started) * 1000),
                )
            raise

    async def close(self) -> None:
        if not self._leased:
            return
        await self.pool.release(self.lease_key)
        self._worker = None
        self._leased = False

    async def _worker_or_acquire(self) -> tuple[KernelWorker, bool]:
        if self._worker is not None and not self._worker.alive:
            await self.pool.drop_leased_worker(self.lease_key, self._worker)
            self._worker = None
            self._leased = False
        if self._worker is not None and self._worker.alive:
            return self._worker, False
        progress = current_progress()
        if progress is not None:
            progress.set_task("Acquiring a Python kernel")
        try:
            self._worker = await self.pool.acquire(self.workspace, lease_key=self.lease_key, env=self.env)
        except KernelPoolBusy as exc:
            raise KernelWorkerError(
                "no python kernel worker available; all workers are busy, try again later"
            ) from exc
        self._leased = True
        return self._worker, True

def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> ExecutePythonTool:
    del config
    env = {key: value for integration_env in context.integrations.values() for key, value in integration_env.items()}
    daemon_url = context.rpc.get("daemon_url")
    if isinstance(daemon_url, str) and daemon_url:
        env["YUUBOT_DAEMON_URL"] = daemon_url
    env["YUUBOT_TASK_OWNER"] = make_owner(context.actor, context.conversation_id)
    env["YUUBOT_ACTOR_ID"] = context.actor
    turn_token = context.rpc.get("turn_token")
    if isinstance(turn_token, str) and turn_token:
        env["YUUBOT_TURN_TOKEN"] = turn_token
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
    ExecutePythonPayload,
    DESCRIPTION,
    _factory,
    _uninstall,
)
