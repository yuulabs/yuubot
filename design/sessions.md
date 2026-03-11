# Session 设计

yuubot 只有一种 session：**Chat Session**。它表示“某个 ctx 下，某个 agent 的一次持续工作”。这里的“工作”不只是聊天历史，还包括当前正在跑的 agent / tool / subagent 流程。

一句话说清楚：

- session 是用户视角的工作单元；
- flow 是框架内部的运行单元；
- 一轮交互完成，不是 LLM 先说完就算，而是这轮里所有子 flow 都完成，并且最终回复已经生成。

---

## Session 是 LLM 专属概念

Session 只与 LLM 交互链路有关。非 LLM 命令（`/ping`、`/help`、`/cost`、`/bot` 等内置命令）**完全绕过 session**，不读取、不修改、不关闭 session，直接执行并返回。仅受 recorder API 限流约束。

这意味着：
- 用户在 LLM session 进行中发 `/ping`，session **不受影响**，ping 直接响应 "pong"
- 非 LLM 命令仍受 `_should_respond` 门控（bot_enabled、DM 白名单等），但不经过 session 路径

---

## 核心概念

### Session

session 是用户看见的连续对话。它绑定 `(ctx_id, agent_name)`，保存：

- 已完成轮次的 history
- 当前 root flow 是否仍在运行
- 当前轮次里暂存的 ping / 用户补充消息
- token 统计、handoff note 等会话级元数据

session 关注的是“这段工作现在处于什么阶段”，而不是具体某个工具调用的内部细节。

### Flow

flow 是统一的运行抽象。下面三种东西本质上都是 flow：

1. root agent
2. tool，例如 `bash`
3. subagent / delegate 出去的子代理

每个 flow 都有：

- `flow_id`
- `parent_flow_id`
- `kind`：`agent | tool`
- `status`：`running | waiting_input | done | error | cancelled`
- `children`
- `ping()` 接口

`handle` 只是 flow 的外部地址，不再是“长任务补丁机制”的专有概念。

### Ping

Ping 是统一的事件注入机制。不要把它理解成“只有 tool 完成时发一句 system message”，而要把它看成 flow 之间互相唤醒、补充信息、继续运行的标准方式。

常见 ping 类型：

- `user_message`：用户在 root agent 仍在处理时又发来新消息
- `child_completed`：某个子 flow 已经完成
- `child_failed`：某个子 flow 出错
- `tool_output`：工具产生了新的可见输出
- `stdin`：给可交互工具写入标准输入
- `context_query`：子代理向父代理索取更精确的上下文
- `cancel`：请求取消某个 flow
- `system_note`：框架内部注入的状态通知

框架内部使用结构化 ping；渲染给 LLM 时，才把它转成适合模型理解的文本。

---

## 一轮交互何时算完成

这是本设计最关键的定义。

一轮交互完成，必须同时满足：

1. 当前轮次启动的所有子 tool flow 都已完成、失败或取消
2. 这些子 flow 的子 flow 也都已经收敛
3. 当前 agent 已经消费了这些完成信号
4. 当前 agent 生成了最终回复

只要某个子 flow 还没完成，这一轮就还在进行中。

换句话说：

- “底层工具返回了 handle” 不等于一轮结束
- “LLM 先说了一句我已经开始处理了” 也不等于一轮结束

如果某个工具超时，只是说明这个 flow 暂时还没完成，后续应该通过 ping 把完成事件送回父 flow，而不是把这轮误判成已经 finished。

---

## Flow Tree

一次 session 的活动可以表示成一棵 flow tree：

```text
Session(ctx=1, agent=main)
└── Root Agent Flow
    ├── Tool Flow: delegate(worker)
    │   └── Agent Flow: worker
    │       └── Tool Flow: execute_bash
    └── Tool Flow: im_send
```

树上的每个节点都是 flow。

父子关系的含义是：

- 父 flow 创建子 flow
- 子 flow 的完成/失败会以 ping 的形式回到父 flow
- 父 flow 在等待子 flow 期间，不视为本轮完成

这比“LLM 调工具 -> 工具返回 still running -> 下一轮人工 check”更符合真实语义。

---

## Root Agent 也是 Flow

root agent 不应被特殊对待。它和子代理遵守同一套规则：

- 可以接收 ping
- 可以被取消
- 可以等待子 flow
- 可以在运行中收到用户补充消息

例如，用户在 root agent 还没完成当前工作时再次发言：

1. dispatcher 检查 `agent_runner.get_active_flow(ctx_id)`
2. 如果有 running root flow → 构建 XML payload → `root_flow.ping(USER_MESSAGE)`
3. loop 在下个 step 开头 drain 到这条 ping，merge 后追加到 agent history
4. 如果 flow 已经跑完 → 回退到 `_CtxWorker` continuation 路径（排队等下一轮）

这不是”下一轮 continuation”，而是”当前未完成轮次收到补充输入”。

实现路径：`dispatcher._ping_or_enqueue()` 做判断，`agent_runner._active_flows` 追踪 ctx → root flow 映射。

---

## Tool Flow

tool 不应该一律被建模成“同步返回字符串的函数”。至少对长任务或交互式工具不是这样。

例如 `bash`：

- 启动 PTY / subprocess 后，它本身就是一个 tool flow
- stdout/stderr 形成 `tool_output` ping
- stdin 通过 `stdin` ping 注入
- 进程退出时发 `child_completed`

这样才支持真正的交互式工具，而不是只能跑一次性命令。

同样，`delegate` 也不是特殊协议，它只是创建了一个子 agent flow。

---

## 子代理与向上询问

子代理和 root agent 用同一套 flow 机制。

子代理不仅可以向下继续创建工具/子代理，也可以向父 flow 发送 ping，例如：

- “当前上下文不够，请补充约束”
- “目标分支不明确，请确认”
- “是否允许中断当前任务并切换方案”

这里的规则是：

- 子代理默认只 ping 直接父 flow
- 是否继续往上询问，由父 flow 决定

这样树结构清晰，也避免任意子节点越级打断 root。

---

## Session 状态

session 只保留最少、直观的状态：

- `idle`：当前没有正在运行的 root flow，可以开始新一轮
- `running`：当前有 root flow 在运行
- `closed`：用户显式结束，或被命令关闭
- `expired`：TTL 到期
- `rolled_over`：因上下文压缩而被新 session 接替

这里不再引入“running handle 需要跨重启恢复”的复杂状态。

原因很简单：

- 正在运行的 tool / agent flow 依赖进程内对象
- Daemon 重启后，这些运行时对象天然消失
- 强行从 DB 恢复它们只会把框架复杂化，而且恢复不出真正的执行上下文

因此，Daemon 重启时：

- 可以恢复 session 的基础元数据（如果未来要持久化 session）
- 但**不恢复正在运行的 flow**
- 这些 flow 统一视为中断

---

## 持久化边界

当前设计下，真正需要持久化的是“用户视角的会话边界”，不是进程内 flow 运行态。

最小持久化内容：

- auto mode 设置
- session 的基础元数据（如果未来决定落盘）
- 已完成轮次的 history
- handoff note

不持久化：

- 正在运行的 tool flow
- 正在运行的 subagent flow
- `asyncio.Task`
- `OutputBuffer`
- PTY / subprocess 句柄

如果 Daemon 在任务运行中重启，正确行为不是“假装能恢复运行”，而是明确地把当前运行态视为中断。

---

## 两种超时的语义区分

系统中有两种超时，语义完全不同：

**工具超时**（execute_bash 的 `timeout` 参数）：命令本身的执行时限。超时后命令被中断（tmux 发送 C-c），返回 `[ERROR] Command timed out`。这是真正的失败——命令没跑完。

**软超时**（agent config 的 `soft_timeout`）：agent loop 等待工具返回的时限。超时后工具继续在后台运行，系统返回 handle。这不是失败——工具还在跑，只是 agent loop 先让出执行权。

## 与 soft timeout 的关系

`soft_timeout` 仍然有用，但它的意义变了：

- 它不再意味着”这一轮结束，返回一个以后再查的 handle”
- 它只意味着”当前子 flow 暂时没完成，父 flow 先让出执行权，等待后续 ping”

LLM 收到 handle 后不需要手动轮询。框架会在子 flow 完成时自动注入一条合成的 `check_running_tool` 调用和结果到 LLM 历史中，让 LLM 看到完成事件。

框架内部保存结构化事件（Ping），渲染给 LLM 时才转成文本。

---

## 实现方向

- `SessionManager` 负责管理 session 生命周期、用户消息队列、TTL 和 rollover
- `FlowManager` 负责管理运行中的 flow tree、父子关系和 ping 分发
- `AgentRunner` 不再把一次 `run()` 当成“完整的一轮”，而是驱动某个 agent flow 前进到下一个稳定点
- `check_running_tool` / `cancel_running_tool` 最终会退化成对某个 tool flow 的通用操作接口，而不是特殊补丁机制

---

## 已拒绝的方案

### 1. 把 running handle 全部持久化到 DB，并在重启后恢复

拒绝原因：

- 恢复不了真正的运行时对象
- 只能恢复一堆“名字像还活着”的元数据
- 会让 session、tool registry、DB 三方耦合得过重

### 2. 只要 tool 返回 handle，就认为这一轮已经完成

拒绝原因：

- 违反“所有子 flow 收敛后才算完成”的定义
- 会把未完成工作伪装成已完成轮次
- 正是当前 `unknown handle` 问题的根源
