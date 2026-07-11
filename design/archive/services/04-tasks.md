> **已过时**：这是历史服务设计，仅供追溯，不得作为当前实现依据。当前权威设计见
> [`design/system-design.md`](../../system-design.md)。

# Design: Runtime Tasks and Task Delivery

**实现顺序：4**（依赖 [01-runtime-events.md](01-runtime-events.md)；**不依赖**
[03-inbound.md](03-inbound.md) 的 HTTP facade，共享 `runtime.wakeup`）

## Scenario

Bot 在 `execute_python` 内启动长时 shell 工作。单次 tool call 受 Harness timeout 约束；
超时或 interrupt 时框架会终止该次 `execute_python` 调用。因此任务注册必须是
**fire-and-forget**：

1. `yb.tasks.submit(...)` 经 loopback HTTP 向 Runtime 注册工作并立刻返回 `Task`。
2. 注册完成后，任务生命周期归 Runtime 所有。
3. 任务进入 terminal state 时，`TaskScheduler` 只 `emit task.finished`；
   `TaskDeliveryListener` 经 `runtime.wakeup.deliver` 把结果投递回 owner conversation。
4. 管理员经管理面前端走 HTTP / WebSocket 查询**当前进程内** registry；`find` /
   `list_tasks` 仅供 LLM 在 `execute_python` 内经 `yb.tasks` facade 查询（facade 实现上经
   loopback HTTP 命中 daemon 端点）。

任务完成默认 TaskDelivery，不依赖 app webhook 或 actor inbound HTTP。应用层可自行组合
（submit 后外部 POST inbound），但 Tasks 模块不 import Inbound adapter。

## Concepts

```text
RuntimeTaskRecord    = 进程内 ephemeral 任务记录；见 Lifecycle
TaskCoroFactory      = (stdin, stdout) -> Awaitable
TaskScheduler        = Runtime.scheduler；创建 asyncio.Task、终态收口、emit task.*
TaskRegistry         = Runtime.tasks；id → RuntimeTaskRecord
TaskDeliveryListener = 常驻 listener；消费 task.finished → runtime.wakeup.deliver
Task                 = execute_python 内 `yb.tasks` facade 返回的句柄
ShellTaskEndpoint    = yb.tasks.submit
```

`TaskScheduler` 与 `TaskRegistry` 为 [01-runtime-events.md](01-runtime-events.md) Runtime
组合成员。调度器与 runner **只 emit**；投递经 listener + `WakeupDelivery`。

## Data Shapes

### Runtime task (generic)

```py
TaskCoroFactory = Callable[[TextStream, TextStream], Awaitable[object]]
TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]

class RuntimeTaskRecord:
  id: str
  owner: str           # actor:{id}:conv:{conversation_id}
  kind: str            # "shell" | ...
  status: TaskStatus
  stdin: TextStream
  stdout: TextStream
  error: str | None
  result: object | None
  delivery_state: Literal["pending", "delivered", "skipped"] = "pending"
```

`coro = coro_factory(record.stdin, record.stdout)`。

### Shell metadata

`kind == "shell"` 时额外字段：`name`, `intro`, `shell`, `exit_code`, `interactive`,
`created_at`, `started_at`, `finished_at`。

Shell runner 使用 **PTY**（`pty_runner.run_pty_process`）：stdout 写入 `TextStream`；
外部经 stdin API 写入 `record.stdin`，runner 泵入进程。适用于交互式 CLI init / login /
bind；**禁止**用 `bash` tool + `timeout_s` 处理同类流程。

### Facade（`execute_python` only）

```py
class Task:
  id: str
  name: str
  intro: str

  async def status(self) -> TaskStatus: ...
  async def output(self, *, max_bytes: int = 65536) -> str: ...
  async def error(self) -> str | None: ...
  async def exit_code(self) -> int | None: ...
  async def write(self, text: str) -> None: ...
  async def cancel(self) -> None: ...
```

### Event kinds

| kind | payload 要点 |
| --- | --- |
| `task.started` | `task_id`, `owner`, `kind`, `name` |
| `task.finished` | `task_id`, `owner`, `kind`, `status`, `error`, `exit_code` |

## Lifecycle（v1 ephemeral）

| 问题 | v1 约定 |
| --- | --- |
| 持久化 | **不**持久化 `RuntimeTaskRecord`；进程重启后 registry 为空 |
| `find` / `list_tasks` | 仅查询当前进程内存；重启后旧 id 返回 `404 not_found` |
| 进程 shutdown | `scheduler.shutdown()`：对 `running`/`pending` 调用 `asyncio.Task.cancel()`；终态统一为 `cancelled`（或 `failed` 若取消前已失败） |
| Actor disable | 取消该 actor 下所有 `running`/`pending` task；终态 `cancelled`；不投递 `task_delivery` |
| `task_delivery` | **至多一次**：`TaskDeliveryListener` 成功 `wakeup.deliver` 后设 `delivery_state=delivered`；mailbox 失败记日志 + `delivery_state=skipped`，不无限重试 |
| `task_delivery` busy | owner conversation `running` 时入 `TaskDeliveryQueue`；`run_loop` / `append_developer_notice` 结束后 `drain_pending_task_deliveries` |
| Harness timeout | 不取消已注册 task；仅结束当前 `execute_python` tool call |
| Server restart | 运行中任务**丢弃**；无恢复、无「标记 failed」的 durable 记录 |

`delivery_state=skipped` 时管理面仍可通过 `GET /api/tasks/{id}` 查看终态 stdout/error，但
owner conversation 不会收到 developer 通知。

## Central Flow

### Runtime task execution

```py
def register_and_schedule(
  *,
  record: RuntimeTaskRecord,
  coro_factory: TaskCoroFactory,
) -> Task:
  runtime.tasks.put(record)
  runtime.scheduler.schedule(record, coro_factory)
  return Task(record.id, runtime=runtime)
```

```py
class TaskScheduler:
  def schedule(self, record: RuntimeTaskRecord, coro_factory: TaskCoroFactory) -> None: ...
  async def shutdown(self) -> None:
    """Cancel all tracked asyncio tasks; await done callbacks."""

  def _on_task_done(self, record: RuntimeTaskRecord, asyncio_task: asyncio.Task) -> None:
    ...
    runtime.emit("task.finished", ...)
```

`cancel` 取消底层 `asyncio.Task`；终态经 `_on_task_done` 统一收口。

### TaskDeliveryListener

```py
runtime.listeners.add(TaskDeliveryListener(runtime))
```

Runner **不得**直接调用 `runtime.wakeup.deliver`。

```py
class TaskDeliveryListener:
  async def on_event(self, kind: str, payload: dict) -> None:
    if kind != "task.finished":
      return
    if payload["kind"] != "shell" or ":conv:" not in payload["owner"]:
      return
    record = runtime.tasks.get(payload["task_id"])
    if record.delivery_state != "pending":
      return
    await schedule_task_delivery(record)
```

```text
shell runner 结束
  → TaskScheduler emit task.finished
  → TaskDeliveryListener → runtime.wakeup.deliver
  → Actor mailbox → run_continuation
```

### Task delivery

```py
async def deliver_task_result(record: RuntimeTaskRecord) -> None:
  actor_id, conversation_id = parse_owner(record.owner)
  text = format_task_delivery(...)
  await runtime.wakeup.deliver(
    WakeupTarget(kind="task_delivery", actor_id=actor_id, conversation_id=conversation_id),
    WakeupPayload(
      text=text,
      source={"task_id": record.id, "task_name": record.name, "status": record.status},
    ),
  )
  record.delivery_state = "delivered"
```

### Actor wake handling (task_delivery)

```py
async def handle_mailbox_message(actor, message: ActorMessage) -> None:
  inbound_kind = message.source.get("inbound_kind")
  if inbound_kind in {"task_delivery", "conversation_callback"}:
    developer = InputMessage(role="developer", name="yuubot", content=[Text(message.text)])
    await conversation.append_and_persist(developer)
    await conversation.run_continuation()
    return
  ...
```

Role 映射权威定义见 [01-runtime-events.md](01-runtime-events.md#actormessage共享-mailbox-契约)。

### Shell submit (fire-and-forget)

`yb.tasks.submit` 经 loopback `POST /api/tasks` 进入下列 handler；handler 内直接调
Runtime，不经 admin 认证（loopback gate 即可）。

```py
async def submit_handler(name, shell, intro, *, owner, wait_s=20) -> Task:
  record = register_shell_task(...)
  task = register_and_schedule(record=record, coro_factory=shell_coro_factory(...))
  if wait_s > 0:
    await runtime.wait_until_terminal_or_timeout(record.id, timeout=wait_s)
  return task
```

## Access paths

| 调用方 | 入口 | 说明 |
| --- | --- | --- |
| 管理员（人类） | 管理面前端 → 下节 HTTP / WS | 不经 `yb.*` facade |
| LLM | `execute_python` → `yb.tasks.*` | facade 经 loopback HTTP 查 daemon 端点；与 admin 路由共享 registry 语义 |

## HTTP / daemon endpoints

Task registry 的 HTTP 面。`yb.tasks` facade 经 loopback 调用下列 handler；管理面前端只使用
其中 GET / cancel 子集（AdminAuth）。错误信封见
[02-admin-boundary.md](02-admin-boundary.md)。

```http
POST /api/tasks                    # loopback only；yb.tasks.submit
GET  /api/tasks                    # admin UI + yb.tasks.list_tasks
GET  /api/tasks/{task_id}          # admin UI + yb.tasks.find
POST /api/tasks/{task_id}/cancel   # admin UI + Task.cancel
POST /api/tasks/{task_id}/stdin    # admin UI + yb.tasks.write
```

**`POST /api/tasks` → `200`** — body：`name`, `shell`, `intro`；`owner` 由 facade 从
conversation context 注入。返回 `Task` 快照（同 `GET /api/tasks/{task_id}` 字段子集）。仅
loopback；管理面 UI 不暴露创建入口。

**`GET /api/tasks` → `200`**

```json
{
  "items": [
    {
      "id": "t-abc",
      "owner": "actor:amy:conv:c1",
      "kind": "shell",
      "name": "fetch-report",
      "status": "running",
      "error": null,
      "exit_code": null,
      "delivery_state": "pending"
    }
  ]
}
```

**`GET /api/tasks/{task_id}`** — 单条同上字段 + `stdout_tail`（最近 64KiB 文本）。

**`POST /api/tasks/{task_id}/cancel` → `200`** — 返回 cancel 后快照；已终态则幂等返回当前状态。

| Status | code | 场景 |
| --- | --- | --- |
| 404 | `not_found` | 未知 task_id（含重启后旧 id） |
| 409 | `conflict` | task 已终态且不可 cancel（可选；v1 cancel 已终态幂等 200） |

WS `task.subscribe` / `task.stdin` / `task.cancel` 帧顺序见 [02-admin-boundary.md](02-admin-boundary.md#websocket-contract)。

## Script API（`execute_python` only）

仅 Bot 在 `execute_python` 内可调用；实现上各方法经 loopback HTTP 命中 daemon handler（与上节
HTTP 路由共享 registry 语义）。管理员不可使用此 API。

```py
async def submit(name: str, shell: str, intro: str) -> Task: ...
def find(task_id: str) -> Task: ...
def list_tasks(*, name_glob: str = "") -> list[Task]: ...
```

`list_tasks` 默认按当前 conversation owner 过滤。

## Context Access

```text
Core needs:
  Runtime.tasks, Runtime.scheduler, runtime.wakeup
  actor_id / conversation_id, workspace

Source:
  tasks, scheduler, wakeup  <- Runtime
  TaskDeliveryListener      <- startup 注册至 ListenerHub

Access path:
  yb.tasks.*        -> loopback HTTP -> daemon task handlers -> TaskRegistry
  Admin frontend    -> GET /api/tasks, WS task.* (AdminAuth) -> TaskRegistry
  task.finished     -> TaskDeliveryListener -> runtime.wakeup.deliver

Missing context: none
Accepted debt:
  submit 当前只覆盖 shell；其它 coro_factory 由 Runtime 内部注册。
```

## Invariants

1. 任务完成默认 TaskDelivery；不依赖 inbound HTTP。
2. `submit` 注册完成后任务归 Runtime；Harness timeout 不取消已注册任务。
3. `task_delivery` 只追加 developer message + `run_continuation`。
4. Runner 与 `TaskScheduler` 不直接调用 `runtime.wakeup.deliver`。
5. `TaskScheduler` 是 Runtime 内创建托管 asyncio.Task 的唯一入口。
6. v1 registry 为 ephemeral；重启不恢复任务状态。
7. `task_delivery` 对每个 `RuntimeTaskRecord` 至多成功投递一次。
8. 人类只经管理面前端访问 task HTTP / WS；`yb.tasks` 仅供 `execute_python` 内 LLM 使用。

## Related

- Runtime 组合与 WakeupDelivery：[01-runtime-events.md](01-runtime-events.md)
- Inbound HTTP（独立）：[03-inbound.md](03-inbound.md)
- Actor JSON KV：[06-kv.md](06-kv.md)
