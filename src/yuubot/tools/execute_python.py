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

This is the preferred tool for multi-step local work, data shaping, and integration facade calls. The session supports native top-level `await`, so async integration APIs can be called directly.

The working directory is the actor workspace. Standard output and standard error are captured and returned as text. An empty capture returns `ok`.

Enabled integrations inject credentials and defaults into the process environment for `yext` facades. Import and use them explicitly, for example:
- `await yext.web.search(query)`
- `await yext.web.read(url)`
- `await yext.web.download(url)`
- `repo = yext.github.repo(); await repo.issues.list_recent()`
- `yb.office.pdf.to_markdown(path)`

For long-running shell work, use the runtime task facade instead of blocking shell in this session:
- `task = await yb.tasks.submit(name, shell, intro)` registers a fire-and-forget shell task with Runtime and returns a Task handle immediately.
- Shell tasks run in a PTY with live stdout and stdin. Use this for interactive CLI init, login, or bind flows.
- Task execution continues under Runtime after this tool call ends; when the task finishes, yuubot appends a developer message and automatically continues the owner conversation.
- Query with `await yb.tasks.find(task_id)`, `await yb.tasks.list_tasks(name_glob=...)`, `await task.output()`, and `await task.status()`.
- Send interactive input with `await task.write(text)` (include newlines when the prompt expects them).
- Cancel with `await task.cancel()`.
- Do not use the `bash` tool with `timeout_s` for interactive or long-running init; timeouts kill the process and can leave partial setup behind.
- Do not call daemon HTTP endpoints such as `/api/tasks`, `/api/inbound`, or admin/public APIs directly; use the `yb.tasks` facade.

MCP data sources:
- Use `import yb.mcps`, then `await yb.mcps.search(query)` to discover enabled MCP tools/resources/prompts.
- Search results intentionally omit parameter schemas. Use `client = yb.mcps.get_client(server_id)` and `await client.get_spec(name)` before invoking a tool.
- Call tools with `await client.invoke(name, **kwargs)` and read resources with `await client.read_resource(uri)`.
- Secrets and raw credentials are daemon-managed and are never available in this Python session.

Scheduled jobs (durable cron):
- Import `yb.tasks` or `yb.tasks.cron`; `yb.tasks.cron` is available as the cron facade.
- `await yb.tasks.cron.add(name, timezone=..., cron=..., action=...)` or `at=...` for one-shot schedules. `timezone` must be an explicit IANA name such as `Asia/Shanghai`; `at` accepts a local ISO datetime like `2026-07-06T11:30:00` or a short relative delay such as `+1m`.
- Action dict examples: `{"kind":"shell","name":"...","shell":"...","intro":"..."}`, `{"kind":"actor_message","text":"..."}`, `{"kind":"conversation_callback","text":"..."}`, `{"kind":"reminder","title":"...","body":"...","channels":[{"kind":"browser"},{"kind":"web_push"}]}`.
- Use `actor_message` for standalone scheduled actor work. Use `conversation_callback` when the scheduled result should continue this exact conversation.
- Manage with `await yb.tasks.cron.list_jobs()`, `await yb.tasks.cron.find(job_id)`, `await yb.tasks.cron.pause(job_id)`, `await yb.tasks.cron.delete(job_id)`.
- Do not call `/api/cron-jobs` directly; use the `yb.tasks.cron` facade.

Dynamic admin pages:
- You may write HTML/CSS/JS under the workspace (for example `projects/.../form.html`). When an admin opens the page in the management UI, page JavaScript may call admin KV and inbound endpoints with AdminAuth.
- `GET` / `PUT` / `DELETE` `/api/actors/{actor_id}/kv/{key}` (`{key}` is URL-encoded; supports `ETag` / `If-Match`)
- `POST` `/api/actors/{actor_id}/inbound` (`text` plus optional `conversation_id`)
- Recommended submit flow: persist draft state to KV, then POST inbound with structured JSON `text` containing `submitted_at`, `source_page`, `purpose` or `context`, optional `kv_key`, and `payload`.
- Do not loopback-call admin HTTP from this session; dynamic pages are browser-driven.

The Python session is reset after each user turn. Variables, imports, open files, and in-memory side effects do not survive into the next turn. A developer notice is added when a previous session is no longer available.

After changing dependencies with `uv add` or `uv remove`, call the `restart_kernel` tool, then continue from workspace files; do not assume packages imported before a dependency change remain available without rerunning imports.

The runtime is headless: `plt.show()` and inline notebook display do not reach the user. Save generated files under the workspace, normally under `artifacts/`. For images, embed the saved file as a Markdown image in your final response when the user should see it inline.

Avoid printing huge outputs; filter to the relevant data before returning."""


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
