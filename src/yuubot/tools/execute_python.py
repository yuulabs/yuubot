"""execute_python tool backed by ipykernel worker subprocesses."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

import msgspec
from attrs import define, field

from ..domain.messages import ConversationContext
from ..python.facade import remove_facade
from ..python.pool import KernelPool
from ..python.worker import KernelWorker, KernelWorkerError
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
- Task execution continues under Runtime after this tool call ends; when the task finishes, yuubot appends a developer message and automatically continues the owner conversation.
- Query and control with `await yb.tasks.find(task_id)`, `await yb.tasks.list_tasks(name_glob=...)`, `await task.output()`, and `await task.cancel()`.
- Do not call daemon HTTP endpoints such as `/api/tasks`, `/api/inbound`, or admin/public APIs directly; use the `yb.tasks` facade.

Scheduled jobs (durable cron):
- `await yb.tasks.cron.add(name, timezone=..., cron=..., action=...)` or `at=...` for one-shot schedules. `timezone` must be an explicit IANA name such as `Asia/Shanghai`.
- Action dict examples: `{"kind":"shell","name":"...","shell":"...","intro":"..."}`, `{"kind":"wakeup","text":"..."}`, `{"kind":"reminder","title":"...","body":"...","channels":[{"kind":"browser"},{"kind":"web_push"}]}`.
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

    async def execute(self, payload: msgspec.Struct) -> str:
        data = cast(ExecutePythonPayload, payload)
        worker = await self._worker_or_acquire()
        try:
            return await worker.run_code(data.code)
        except KernelWorkerError:
            if self._worker is not None:
                await self.pool.drop_leased_worker(self.lease_key, self._worker)
                self._worker = None
                self._leased = False
            worker = await self._worker_or_acquire()
            return await worker.run_code(data.code)

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
        self._worker = await self.pool.acquire(self.workspace, lease_key=self.lease_key, env=self.env)
        self._leased = True
        return self._worker


def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> ExecutePythonTool:
    del config
    env = {key: value for integration_env in context.integrations.values() for key, value in integration_env.items()}
    daemon_url = context.rpc.get("daemon_url")
    if isinstance(daemon_url, str) and daemon_url:
        env["YUUBOT_DAEMON_URL"] = daemon_url
    env["YUUBOT_TASK_OWNER"] = make_owner(actor_id=context.actor, conversation_id=context.conversation_id)
    db_path = runtime.db_dir / "yuubot.db"
    env["YUUBOT_DB_PATH"] = str(db_path)
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
