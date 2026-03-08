# yuubot 架构总览

## 系统定位

yuubot 是 yuuagents 的增强层，提供：
1. **Skills** — 为 agent 提供 IM、Web、Memory 等能力（CLI 工具形式）
2. **消息驱动** — 接收 QQ 消息，解析命令，触发 agent
3. **QQ Bot 接口** — 通过 NapCat (OneBot V11) 对接 QQ

## 进程架构

```
┌──────────┐    反向WS     ┌──────────────┐   内部WS转发   ┌──────────────┐
│  NapCat  │ ──────────▶  │   Recorder   │ ────────────▶ │ yuubot Daemon│
│ (独立部署) │              │ (落盘+转发)    │               │ (Agent驱动)   │
└──────────┘              └──────┬───────┘               └──────┬───────┘
                                 │                              │
                                 ▼                              │ 调用
                          ┌──────────┐                   ┌──────▼───────┐
                          │  SQLite  │ ◀─────────────── │  ybot CLI    │
                          │ (消息DB)  │    im search等    │  (Skills)    │
                          └──────────┘                   └──────────────┘
```

### 三个进程

| 进程 | 职责 | 生命周期 |
|------|------|----------|
| **NapCat** | 维持 QQ 登录态，提供 OneBot V11 协议接口 | 独立运行，不随 bot 重启 |
| **Recorder** | 接收 NapCat 反向 WS → 消息落盘 SQLite + 转发给 daemon | 与 NapCat 绑定启停，保证不丢消息 |
| **Daemon** | 接收 Recorder 转发的消息 → 命令解析 → 触发 Agent → Agent 通过 CLI skills 操作 | 可独立重启，不影响消息落盘 |

### 设计理由

- **Recorder 与 NapCat 绑定**：实践中经常需要停掉 bot 进程调试，但不希望 NapCat 掉线（重新登录 + 风控很麻烦）。Recorder 保证消息持续落盘不丢失。
- **Recorder 转发给 Daemon**：NapCat 反向 WS 通常只配一个目标。Recorder 作为唯一接收端，负责转发给 daemon。
- **消息查询基于落盘数据**：`ybot im search` 等 CLI 查询的是 SQLite 中的落盘消息，而非实时从 QQ 拉取。

## 消息流

### 被动模式（收到消息触发）

```
QQ用户发消息
  → NapCat 通过反向WS推送事件
  → Recorder 接收：
      1. 解析消息，写入 SQLite（落盘）
      2. 分配/查找 ctx_id
      3. 通过内部WS转发给 Daemon
  → Daemon 接收：
      1. 命令解析（树形命令匹配）
      2. 权限检查（Role系统）
      3. 如果匹配到命令 → 触发 Agent
  → Agent 执行：
      1. 通过 ybot CLI skills 操作（im send, web read 等）
      2. Skills 读写 SQLite / 调用外部服务
      3. 通过 Recorder 的 HTTP API 发送消息回 NapCat
```

### 主动模式（定时触发）

```
Cron/定时任务触发
  → Daemon 拉起 Agent
  → Agent 自行决定是否需要发送消息
  → 如需发送，必须明确指定 ctx_id 或 uid
```

## 消息格式

统一的消息段格式（JSON 数组）：

```json
[
    {"type": "text", "text": "hello"},
    {"type": "image", "url": "https://example.com/image.jpg"},
    {"type": "at", "qq": "123456"}
]
```

## ctx_id 机制

- ctx_id 是一个**自增整数**（避免 UUID 太复杂导致 LLM 犯错）
- 当一个群聊/私聊的消息**第一次被收到**时，由 Recorder 分配
- 映射关系存入 SQLite，Recorder 启动时热加载到内存
- Agent 通过 ctx_id 定消息去向，无需关心底层的群号/QQ号

## 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| QQ 协议 | NapCat (OneBot V11) | 成熟稳定 |
| 通信 | WebSocket (反向WS) | 实时推送，被动模式必需 |
| 数据库 | SQLite | 轻量，单机部署，无需额外服务 |
| CLI 框架 | Click | 成熟稳定，生态丰富 |
| 异步框架 | FastAPI + asyncio | Daemon 对外暴露 HTTP 接口，内部异步处理 |
| Agent SDK | yuuagents SDK | 核心依赖，提供 Agent/Tool/Skill 能力 |
| 搜索引擎 | Tavily API | yuuagents 已有 tavily_api_key 支持 |
| 网页抓取 | Playwright + Trafilatura | 已有 agent_read.py 参考实现 |
| 定时任务 | APScheduler / asyncio 内置 | Cron 表达式触发主动模式 |
