# Design: Runtime Events and Listeners

**实现顺序：1**

## Scenario

Runtime 内各业务组件需要向外暴露状态变化，但不应与 WebSocket 推送、外部副作用或后续动作
耦合。第一步建立统一事件出口、listener 消费层，以及 Actor mailbox 的共享 wakeup 投递。

## Concepts

```text
EventBus         = 进程内事件总线；ring buffer 保留最近条目供调试与 bootstrap 快照
Runtime.emit     = 业务组件对外发信号的唯一直接出口
ListenerHub      = 所有 eventbus 消费者的注册表与分发循环
Listener           = 消费事件的协议；观测、向外部推送、触发后续动作均实现为此
WsListener         = 单条 WebSocket 连接的动态 listener；按客户端 subscribe 过滤并 push frame
TextStream         = 可增量订阅的文本输出；stdout 等观测面经 subscribe，不经 eventbus 逐字节推送
WakeupDelivery     = 向 Actor mailbox 投递 ActorMessage 的唯一 Core 入口
ActorMessage       = mailbox 单元；Actor 串行消费（见 Data Shapes）
TaskRegistry       = 进程内 RuntimeTaskRecord 索引（ephemeral，见 04-tasks.md）
TaskScheduler      = 创建托管 asyncio.Task、终态收口、emit task.*（见 04-tasks.md）
ShareRegistry      = ShareGrant + published 快照（见 05-share.md）
KvStore            = Actor 命名空间 JSON 文档（见 06-kv.md）
```

## Runtime composition

```text
Runtime = {
  eventbus: EventBus,
  listeners: ListenerHub,
  wakeup: WakeupDelivery,
  tasks: TaskRegistry,
  scheduler: TaskScheduler,
  shares: ShareRegistry,
  kv: KvStore,
  mailboxes: ActorMailboxRegistry,
  ...
}

def emit(self, kind: str, **payload) -> None:
  self.eventbus.emit(kind, **payload)
```

`emit` 与 `wakeup.deliver` 是 Runtime 级能力；各子系统经 `runtime` 引用调用，不在
Conversation / Task 等类型上挂 listener 或 mailbox API。

### Runtime lifecycle

**Startup**（单进程、单 asyncio loop）：

```text
1. 构造 EventBus, ListenerHub, WakeupDelivery, TaskRegistry, TaskScheduler,
   ShareRegistry, KvStore, ActorMailboxRegistry, …
2. 组装 Runtime；注册 Gateway、Integration 等引用
3. listeners.add(常驻 listener)   # 如 TaskDeliveryListener（04 注册）
4. await listeners.start()          # 开始消费 eventbus
5. 启动 Actor 调度循环、rebind durable state
```

**Shutdown**（`POST /api/admin/shutdown` 或进程信号）：

```text
1. 拒绝新 HTTP/WS（或仅保留 healthz）
2. await scheduler.shutdown()       # 取消运行中 task；见 04-tasks.md
3. await listeners.stop()           # 排空 hub 队列；不再分发
4. 关闭 WS 连接（remove 各 WsListener）
5. flush durable state（ApplicationState、KV、ShareGrant 已由各写路径持久化）
6. 进程退出
```

后序模块只追加「步骤 3 注册 listener」或「步骤 2 扩展 scheduler 行为」，不改变上述所有权。

## Data Shapes

### RuntimeEvent

```py
class RuntimeEvent(msgspec.Struct, frozen=True):
  kind: str
  payload: dict
  ts: str
```

```py
class EventBus:
  def emit(self, kind: str, **payload) -> None: ...
  @property
  def events(self) -> list[RuntimeEvent]: ...  # ring buffer，v1 保留最近 100 条
```

### ActorMessage（共享 mailbox 契约）

所有经 mailbox 唤醒 Conversation 的路径（inbound、task delivery、未来 callback）使用同一
shape。实现须与 `src/yuubot/models.py` 对齐并扩展 `source`。

```py
class ActorMessage(msgspec.Struct, frozen=True):
  text: str
  conversation_id: str | None = None
  source: dict = {}   # 投递方元数据；WakeupDelivery 保证 inbound_kind
```

`source` 约定字段：

| 字段 | 写入方 | 含义 |
| --- | --- | --- |
| `inbound_kind` | `WakeupDelivery` | `app_webhook` \| `actor_inbound` \| `task_delivery` \| `conversation_callback` |
| 其它 | 调用方 `WakeupPayload.source` | 平台 message id、task_id 等；Actor 可读，不保证 schema |

**Role 映射**（Actor `handle_mailbox_message` 统一分支）：

| `inbound_kind` | History 效果 | 进入 loop 方式 |
| --- | --- | --- |
| `app_webhook`, `actor_inbound` | 追加 **user** `InputMessage` | `conversation_id=None` 时由 Actor 默认 inbound loop 复用/新建 conversation；然后 `run_loop(user_input_from_actor_message(message))` |
| `task_delivery`, `conversation_callback` | 追加 **developer** `InputMessage`（`name="yuubot"`） | `run_continuation()` |

`conversation_callback` 必须绑定明确的 `conversation_id`，用于任务结果或 cron 回调继续 owner conversation。Cron legacy `wakeup` 仅作为兼容输入，执行时按 `actor_inbound` 语义投递，不再作为长期语义使用。

### WakeupDelivery

```py
class WakeupTarget(msgspec.Struct, frozen=True):
  kind: str              # 写入 source["inbound_kind"]
  actor_id: str
  conversation_id: str | None = None

class WakeupPayload(msgspec.Struct, frozen=True):
  text: str
  source: dict = {}

class WakeupDelivery:
  async def deliver(self, target: WakeupTarget, payload: WakeupPayload) -> None:
    await self._mailboxes.get(target.actor_id).send(
      ActorMessage(
        text=payload.text,
        conversation_id=target.conversation_id,
        source=payload.source | {"inbound_kind": target.kind},
      )
    )
    self._runtime.emit(
      "wakeup.delivered",
      actor_id=target.actor_id,
      inbound_kind=target.kind,
    )
```

Inbound HTTP（03）与 `TaskDeliveryListener`（04）均调用 `runtime.wakeup.deliver`，不直接
`mailbox.send`。

### Listener

```py
class Listener(Protocol):
  async def on_event(self, kind: str, payload: dict) -> None: ...
```

### ListenerHub

Hub 从 `EventBus` 拉取事件，串行入队后分发给已注册 listener。语义如下。

```py
class ListenerHub:
  def add(self, listener: Listener) -> None: ...
  def remove(self, listener: Listener) -> None: ...
  async def start(self) -> None: ...
  async def stop(self) -> None: ...
```

| 维度 | v1 语义 |
| --- | --- |
| 队列 | 单 `asyncio.Queue`；`EventBus.emit` 非阻塞入队 |
| 分发 | `start` 后单 loop task 取事件；对每个**当前已注册** listener 调用 `on_event` |
| 异常隔离 | 某 listener 抛错：记录日志，继续其余 listener；不 re-raise 到 eventbus |
| 阻塞策略 | `on_event` 应尽快返回；耗时副作用须 `asyncio.create_task` 或自有队列 |
| `add` | 可在 `start` 前后调用；`start` 后 `add` 的 listener 从**下一事件**起接收 |
| `remove` | 幂等；正在执行的 `on_event` 不中断，之后不再投递 |
| `add`/`remove` 并发 | 与分发 loop 同线程（asyncio）；`add`/`remove` 用同步结构更新注册表，分发前快照 listener 列表 |
| `stop` | 置停止标志；排空或丢弃队列中未分发事件（v1：排空已入队项后退出）；不再 `add` 新分发 |

### TextStream

```py
class TextStream:
  def write(self, chunk: str) -> None: ...
  def tail(self, *, max_bytes: int) -> str: ...
  def subscribe(self) -> AsyncIterator[str]: ...
```

## Central Flow

### Emit path

```py
runtime.emit(
  "conversation.stream",
  conversation_id=conversation.id,
  event=chunk,
)
```

```text
业务组件 state change
  → runtime.emit(kind, **payload)
  → EventBus（写入 ring buffer）
  → ListenerHub 分发给所有已注册 listener
```

### Resident vs dynamic listeners

```text
进程 startup（见 Runtime lifecycle）
  → 注册常驻 listener（add）
  → listeners.start()

WebSocket accept
  → 创建 WsListener(connection)
  → runtime.listeners.add(ws_listener)

WebSocket disconnect
  → runtime.listeners.remove(ws_listener)
```

### WsListener（管理面观测）

每条 WebSocket 连接对应一个 `WsListener`。帧格式与认证见
[02-admin-boundary.md](02-admin-boundary.md#websocket-contract)。

```text
WsListener 典型 filter
  track_send(command_id, conversation_id)     → conversation.stream / output / tool_results
  track_history(conversation_id)              → conversation.history.append
  track_events(kinds)                         → runtime.event
  track_task(task_id)                         → task.event
```

连接断开时必须 `remove`，不再 push。

### TextStream 观测

长输出写入 `TextStream`。全量 tail 供快照 API；增量经 `subscribe()` 供 `WsListener` 转成
`task.event` frame。

## Event kind catalog（v1 出口）

| kind | payload 要点 |
| --- | --- |
| `conversation.input` | `conversation_id`, `content` |
| `conversation.stream` | `conversation_id`, `event` |
| `conversation.output` | `conversation_id`, `reason` |
| `conversation.tool_results` | `conversation_id`, `count`, `results` |
| `conversation.cost` | `conversation_id`, token fields, `payg_cost`, `estimated` |
| `conversation.history.append` | `conversation_id`, `item` |
| `wakeup.delivered` | `actor_id`, `inbound_kind` |
| `share.created` | `share_id`, `actor_id` |

`task.*` 由 [04-tasks.md](04-tasks.md) 定义；`gateway.*` 由 Integration 层定义。

### Tool result stream

长阻塞工具必须通过 `conversation.stream` 暴露过程输出，使用与 LLM token stream 相同的
`StreamEvent` 外形：

| stream kind | payload 要点 |
| --- | --- |
| `tool_result_delta` | `tool_call_id`, `tool_name`, `text` |
| `tool_result_end` | `tool_call_id`, `tool_name`, `content` |

约束：

1. `tool_result_delta.group_id` 和 `tool_result_end.group_id` 都是实际 `tool_call_id`。
2. `tool_result_delta` 是实时 UI 事件，不写 History；bash 的 PTY stdout 与
   execute_python 的 kernel stdout/stderr 都走这里。`tool_result_delta.payload.text`
   为终端渲染后的全量 snapshot，而非 raw PTY chunk。
3. 每个产生 `ToolResult` 的调用都必须发 `tool_result_end`，包括正常完成、参数校验失败、
   tool 异常、超时和中断。`tool_result_end.payload.content` 必须等于最终
   `ToolResult.content` 的 wire 形态。
4. `conversation.tool_results` 在所有当前 tool calls 收束后继续发送，payload 带完整
   `results[]`。它是批量通知与兼容出口；前端渲染同一 `tool_call_id` 时必须将 completed
   内容替换 running delta 内容或去重。

## Context Access

```text
Core needs:
  EventBus, ListenerHub, WakeupDelivery, Runtime.emit

Source:
  eventbus, listeners, wakeup  <- Runtime（进程 startup 构造）

Access path:
  business component -> runtime.emit -> EventBus -> ListenerHub -> Listener.on_event
  inbound / task delivery -> runtime.wakeup.deliver -> Actor mailbox
  WS accept -> WsListener -> listeners.add -> filter -> push frame

Missing context: none
Accepted debt:
  eventbus ring buffer 大小 v1 固定 100；不做持久化。
  WsListener 各 command 的 payload 字段随 conversation/runtime 演进；帧顺序见 02。
```

## Invariants

1. 业务核心组件只 `runtime.emit`；不得在 Conversation、调度器、registry 等类型上暴露
   `on_event`、WS push 或外部副作用入口。
2. 观测、向客户端推送、触发后续动作（含 mailbox 投递）一律经 listener 或 `WakeupDelivery`。
3. 新增观测能力优先加 listener，不扩展 `Runtime` 公开字段（`wakeup`/`tasks`/`kv` 等组合
   成员在 01 已列出，实现时一次构造）。
4. `ListenerHub` 分发不得因单个 listener 失败而停止。
5. 动态 `WsListener` 生命周期与 WebSocket 连接绑定；断开必须 `remove`。
6. 除 `WakeupDelivery` 外，任何模块不得直接向 Actor mailbox `send`。
7. `ActorMessage.source["inbound_kind"]` 决定 user vs developer role；投递方不得省略 kind。

## Related

- 下一实现：[02-admin-boundary.md](02-admin-boundary.md)
- Inbound HTTP 入站：[03-inbound.md](03-inbound.md)
- Task 调度与投递：[04-tasks.md](04-tasks.md)
