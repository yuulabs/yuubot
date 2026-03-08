# Session 设计

yuubot 有两种 session，解决不同问题：

- **Chat Session** — 用户与 bot 的多轮对话状态，维护在 Daemon 进程内存中。
- **Agent Session** — agent 内部的异步子任务，维护在 yuuagents SessionRegistry 中。

两者通过 TTL 延长机制联动：当 Agent Session 活跃时，Chat Session 不会过期。

---

## Chat Session

Chat Session 让用户和 bot 进行多轮对话，而不需要每次都输入 `/yllm` 命令。

### 生命周期

1. **创建**：用户发送 `/yllm` 命令时创建，绑定到 `(ctx_id, agent_name)`。每个 ctx 同时只能有一个 chat session。
2. **延续**：在 TTL 内，后续消息自动归入同一 session，agent 能看到完整对话历史。
3. **过期**：最后一次活跃后 300 秒（5 分钟）无新消息则自动过期。如果有活跃的 Agent Session，TTL 会被延长。
4. **主动关闭**：发送非 LLM 命令（如 `/bot`、`/help`）会立即关闭当前 session。
5. **Token 上限**：累计 token 达到 60000 时自动关闭。

### 消息归入规则

不是所有消息都会归入 session，规则取决于聊天类型：

| 场景 | 是否归入 session |
|------|-----------------|
| 私聊，有活跃 session | 是 |
| 群聊，@bot，有活跃 session | 是 |
| 群聊，不 @bot，有活跃 session | 否（忽略） |
| 群聊，`/yllm` 命令 | 创建新 session |
| 任何非 LLM 命令 | 关闭 session，执行命令 |

关键点：**群聊中必须 @bot 才能延续 session**，普通群消息不会被归入。这避免了群里其他人的闲聊污染对话上下文。

### 实现

- `SessionManager`（`daemon/session.py`）：管理所有 chat session 的创建、查询、过期、关闭。
- `Dispatcher`（`daemon/dispatcher.py`）：在命令匹配前检查是否有活跃 session，决定消息走 session 延续还是新命令。

---

## Agent Session（异步子任务）

delegate 工具是同步的——调用方阻塞等待子 agent 完成。对于耗时任务（如编码），这不可接受。AsyncSession 让主 agent 能"发射后不管"，后续轮询或等待完成通知。

## 架构

```
Main Agent → launch_agent("coder", task) → AgentSession(后台 asyncio.Task)
  coder agent:
    execute_bash("background run 'claude -p \"task\" ...'")
    → bg-id
    execute_bash("background drain bg-id")
    → 结果
  coder 完成 → on_complete → 可唤醒主 agent
```

## 核心组件

### AgentSession (yuuagents/session.py)

包装 agent loop 为后台 asyncio task：
- `start()`: 创建 asyncio.Task 运行 agent loop
- `interrupt()`: 设 CANCELLED + cancel task
- `resume()`: 从已有 history 重新进入 loop
- `progress()`: 从 history 提取 assistant 消息
- `result()`: 最终 assistant 文本

### SessionRegistry (yuuagents/session.py)

全局注册表，管理所有活跃 session，提供 create/get/list_active/stop_all。

### Session Tools (yuuagents/tools/session_tools.py)

| 工具 | 功能 |
|------|------|
| `launch_agent` | 异步启动子 agent session，返回 session_id |
| `session_poll` | 查询 session 状态和进展 |
| `session_interrupt` | 中断 session |
| `session_result` | 获取完成 session 的结果 |
| `sleep` | 等待指定秒数，用于轮询间隔 |

### background CLI (Docker 容器内)

预装在 yuuagents-runtime 镜像中的 Python 脚本，让容器内的 agent 能后台运行长时间命令：

```bash
background run <command>       # fork 守护进程，返回 JSON {id, status}
background tail <id>           # 最近 N 行输出
background drain <id>          # 完成则返回全部输出，否则返回当前缓冲
background wait <id> [<id>...] # 阻塞等待完成
background kill <id>           # 终止
background list                # 列出所有任务
```

## yuubot 集成

### TTL 延长

yuubot 的 SessionManager 在有活跃 AgentSession 时自动延长会话 TTL，避免编码任务进行中会话超时。

### _SessionManagerBridge

桥接 yuuagents SessionRegistry 到 yuuagents context 的 SessionManager protocol，在 agent_runner.py 中实现。

### Docker Shell

`ybot docker shell` 提供手动进入容器的入口，方便安装工具、配置 API key。

## Coder Agent

配置在 yuuagents.config.yaml，使用 `{background_cli_prompt}` 变量注入 background CLI 使用说明。通过 `execute_bash` + `background` 运行外部编码工具（如 claude）。
