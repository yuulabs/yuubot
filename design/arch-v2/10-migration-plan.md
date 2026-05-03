# 10. 迁移与实现计划

v2 不要求一次性重写所有东西。推荐按兼容优先、资源模型优先的顺序推进。

## Phase 0: 文档和边界修正

- 明确 trace 两端口：collector/ui。
- 在当前文档中停止使用“去掉 8782”这种表述，改为“不对公网暴露 UI port”。
- 明确 Bootstrap Config vs Runtime Resources。
- 明确 Character vs Actor。
- 明确 Web Chat DB commit ack。
- 在 Gateway checklist 中加入 `UNIQUE(channel, key)` 迁移提醒。

## Phase 1: Resource Registry 基础

新增 DB 表或先用兼容实现：

- `secrets`
- `llm_providers`
- `service_providers`
- `characters`
- `actors`
- `channels`
- `route_rules`

实现统一 ResourceService：

```text
validate
  -> write DB
  -> bump version
  -> update in-memory registry
  -> notify components
```

## Phase 2: Provider UI

- Admin UI 支持创建 LLM provider。
- 支持内置模板。
- 支持 OpenAI-compatible。
- 支持 secret 加密。
- 支持 test connection。
- 兼容从 `llm.yaml` seed provider。

## Phase 3: Character / Actor UI

- Character 页面：clone builtin、section editor、reset builtin。
- Actor 页面：选择 character、provider/model、runtime policy、resource policy。
- 迁移 `agent_llm_refs.<character>` 到 default Actor model binding。

兼容策略：

```text
如果 DB actors 为空：
  从 builtin characters + agent_llm_refs seed actors。
```

## Phase 4: Channel / Route UI

- Web channel 默认 seed 并启用。
- Discord channel 接入流程。
- QQ/OneBot channel 接入流程。
- Route defaults：private/group/thread/other。
- Context pin/reassign。

## Phase 5: Web Chat Queue

- Admin `/ws/chat` 写 DB 队列。
- DB commit 后 ack。
- daemon 消费 pending queue。
- crash recovery：重试 pending/expired processing。

## Phase 6: Trace Proxy Fix

- Bootstrap Config 增加 `trace.collector_port` 和 `trace.ui_port`。
- Admin `/monitor/` 代理到 UI port。
- UI port 不直接公网暴露。

## Phase 7: Bridge

- registration token。
- client verifies server。
- tunnel key / command key 分离。
- node registry。
- heartbeat。
- `yb.bridge_*` master-only API。

## Phase 8: Migrations

当前代码中存在手写 compatibility workaround。v2 需要正式迁移机制：

- 每次 schema change 有 migration。
- migration 可重复执行。
- Docker entrypoint 启动前运行 pending migrations。
- contexts 加 `UNIQUE(channel, key)` 前先清理重复数据。

## 初始 P0/P1 列表

P0：

- trace 两端口配置与 monitor proxy 文档/实现对齐。
- `admin.secret` 非空检查和 localhost 限制。
- `/ychar config` 持久化修复，直到 Actor 模型落地前保持兼容。
- contexts duplicate 检查脚本。

P1：

- Provider DB + UI。
- Secret store。
- Actor seed from existing config。
- Web Chat history load + DB commit ack。
- Runtime resource reload service。

P2：

- Character editor。
- Route UI。
- Channel connect flow。
- Trace deployment cleanup。

P3：

- Bridge Gateway。
- Bridge client。
- DB migration framework。
