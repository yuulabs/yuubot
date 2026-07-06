# Services Design — Implementation Order

按实现顺序阅读与落地。后序文档可引用前序能力，但前序不依赖后序。

| 顺序 | 文档 | 职责 |
| --- | --- | --- |
| 1 | [01-runtime-events.md](01-runtime-events.md) | Runtime 组合、`EventBus`、`ListenerHub`、`WakeupDelivery`、`ActorMessage` |
| 2 | [02-admin-boundary.md](02-admin-boundary.md) | `admin_url_base` / `public_url_base`、AdminAuth、HTTP 错误信封 |
| 3 | [03-inbound.md](03-inbound.md) | App webhook（公网）与 Actor inbound（管理面） |
| 4 | [04-tasks.md](04-tasks.md) | 托管 shell 任务；`task.*` 事件 + `TaskDeliveryListener` |
| 5 | [05-share.md](05-share.md) | Workspace 目录快照为公网只读静态站 |
| 6 | [06-kv.md](06-kv.md) | Actor 命名空间 JSON KV；管理面 HTTP（供 LLM 动态 HTML） |
| 7 | [07-cron.md](07-cron.md) | 持久化 Cron 调度、提醒推送、`yb.tasks.cron` |
| 8 | [08-python-kernel.md](08-python-kernel.md) | ipykernel worker pool、`execute_python` 子进程隔离、`restart_kernel` |
| 9 | [09-observability.md](09-observability.md) | OTEL GenAI tracing、`app_spans`、streaming timing、cost 聚合 API |
| 10 | [10-data-sources.md](10-data-sources.md) | MCP data source、credential store、coding CLI PTY 登录、Skills |

## Runtime 组合（权威定义见 01）

```text
Runtime owns:
  eventbus: EventBus
  listeners: ListenerHub
  wakeup: WakeupDelivery
  tasks: TaskRegistry
  scheduler: TaskScheduler
  cron_jobs: CronJobStore
  cron: CronJobScheduler
  kv: KvStore
  kernels: KernelPool
  observe: ObserveRuntime
  (+ actors, mailboxes, gateway, …)
```

启动与关闭顺序见 [01-runtime-events.md](01-runtime-events.md#runtime-lifecycle)。

## 关系说明

- **事件与 listener** 是所有业务模块的公共底座：核心只 `emit`，观测与副作用经 listener。
- **WakeupDelivery**（01）是 Inbound 与 Tasks 的共享投递层：二者可独立交付 HTTP/facade 与业务
  逻辑，但都经 `runtime.wakeup.deliver` 写入 Actor mailbox，不互相 import 实现细节。
- Inbound 负责外部 HTTP 入站解析；Tasks 负责 shell 托管与终态通知。Tasks **依赖**
  `WakeupDelivery`，**不依赖** Inbound 的 adapter 或 webhook facade。
- LLM 可在 `execute_python` 内组合 `yb.tasks.submit` 与外部 inbound 回调；那是应用层组合，不是
  模块硬耦合。
- LLM 可向 workspace 写入动态 HTML，经 KV HTTP 与 actor inbound 组合实现「写页 → 用户提交 →
  唤醒继续处理」；见 [06-kv.md](06-kv.md) Scenario。
- **Python kernel**（08）：`execute_python` 经 `KernelPool` 在 workspace `.venv` ipykernel 子进程执行；`restart_kernel` 换 import cache。
- **可观测性**（09）依赖 08：跨进程 OTEL 经 daemon ↔ ipykernel 传播；Monitor 读 `/api/usage/*`；
  深度分析经 `yb.observe` + profiling skill。
- 部署级 URL 公式、反代示意、磁盘根路径见
  [deployment-design.md](../deployment/deployment-design.md)。
- **Wire contract 权威来源**：本目录各服务文档 + [02-admin-boundary.md](02-admin-boundary.md)
  的 HTTP/WS 信封与认证。[archive/service-surface.md](../archive/service-surface.md) 仅为历史
  参考，含 legacy path（如 `POST /api/inbound/{integration_type}`），不得作为 implementation 依据。
