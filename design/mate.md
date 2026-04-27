# Mate 设计

## 核心抽象

Mate 是"拥有持久化人格的具名实体"。对外表现为永远在线、永远可寻址，内部在需要时临时拉起一个 Agent turn，turn 结束后 Agent 消亡，但 Mate 的持久状态保留。

**Mate = Character + 持久化层**

每个 Mate 有：
- 唯一**人名**（如 Saki、John），用于寻址——是真正的人名，不是职位或角色名
- 一个信箱（mailbox），消息队列
- 一个工作区（workspace），磁盘目录，跨 turn 持久
- 一条对话历史，用于 rollover（实现细节）
- 一个 Character，决定人格和行为

Mate 没有持久 kernel session（Python 内存状态随 Agent turn 结束而消亡，类比 RAM 断电；工作区文件保留，类比磁盘）。

## 对称性：所有参与者都是 Mate

`maid` 是 Mate #0，系统启动时自动创建，名称固定。人类消息是一种特殊的信件（from: "human"）。所有通信走同一套信箱机制，没有特例。

```
human ──→ maid.mailbox
maid  ──→ saki.mailbox
saki  ──→ maid.mailbox   (reply)
system ──→ maid.mailbox  (timeout 通知)
```

## 信箱模型

每个 Mate 一个信箱，不共享。消息结构：

```python
{
  "from": "maid",          # 发方 mate name，或 "human" / "system"
  "reply_to": "maid",      # 回复应投递到哪个信箱
  "content": "...",
  "ticket_id": "uuid",     # 关联 deadline，reply 时携带以取消 timer
  "sent_at": "...",
  "deadline_at": "...",    # 可选，见超时机制
}
```

唤醒条件：信箱非空即触发 Mate 的 Agent turn。

## 超时机制

`send_to_mate(name, content, deadline="30m")` 发信时自动注册 deadline。

- 收到对应 reply（ticket_id 匹配）→ deadline 取消，无事发生
- deadline 到期仍无 reply → 系统往**发方信箱**投一条 timeout 通知：
  ```python
  {"from": "system", "type": "timeout", "ticket_id": "...", "context": "message to saki, sent at T"}
  ```

Mate 醒来后看到 timeout 消息，自行决定重试、上报还是放弃。超时判断逻辑完全在发方，系统只负责投递通知。

## 持久性与副作用

`create_mate` 是具有持久副作用的操作。Mate 一经创建即写入 DB，独立于创建它的 Agent turn 的生命周期：

- Agent turn 结束 → Mate 继续存在
- Rollover 发生 → Mate 继续存在
- Daemon 重启 → Mate 继续存在
- 唯一的销毁方式是显式调用 `remove_mate`

Agent 应将 `create_mate` 类比于"雇用一位团队成员"而非"创建一个局部变量"。`list_mates` 可在任意 turn 查看当前存活的所有 Mate。

## Rollover

当 Mate 的对话历史接近 context 上限时，自动触发 rollover：压缩历史为摘要，以摘要作为新 Agent turn 的起始上下文。对调用方不可见，Mate 的生命周期在外部看来是无限的。

## API（agent_fns 层）

```python
yb.create_mate(name, character, workspace=None, description=None)
yb.send_to_mate(name, content, deadline="1h")   # 返回 ticket_id
yb.remove_mate(name)
yb.list_mates()                                 # 返回各 Mate 状态摘要
yb.get_mate_status(name)                        # 见可观测性
```

## 可观测性

状态分两层，均由 runtime 层自动维护，不需要 Agent 主动上报。

**Liveness**（DB 字段）：
- `status`: `idle | running | error`
- `last_heartbeat`: 最近一次 Agent step 完成时间
- `error_info`: 最近一次崩溃信息

**Progress**（runtime 实时状态）：
- `frontier`: 当前正在执行的 tool call（tool name、输入摘要、已执行时长）
- `last_completed`: 最近完成的 step 摘要

`get_mate_status(name)` 返回：
```python
{
  "status": "running",
  "last_heartbeat": "2min ago",
  "frontier": {
    "tool": "execute_python",
    "input": "result = yb.web_search('...')",
    "elapsed": "47s"
  },
  "last_completed": {
    "tool": "execute_python",
    "summary": "searched X, found 3 results"
  },
  "error_info": None
}
```

HTTP 端点 `/mates/{name}/status` 暴露同一份数据供人工观察。

frontier 直接映射 trace span 的 open/close 状态，与现有 traces 系统天然对齐。

## maid 的决策模式

maid 收到 timeout 通知后的典型处理：

1. `get_mate_status("saki")` 查看状态
2. `frontier.elapsed` 正常且 `last_heartbeat` 新鲜 → 还在跑，等待或延长 deadline
3. `last_heartbeat` stale 且 `status == "running"` → 大概率崩溃，考虑重启或上报
4. `status == "error"` → 读 `error_info`，决定是否重试

## 实现要点（待细化）

- Mate 记录存 DB（`core/db.py`），字段：`name, character, workspace_path, status, last_heartbeat, error_info`
- 信箱为 DB 表 `mate_messages`，字段含 `from, reply_to, ticket_id, deadline_at, acked_at`
- 后台 job 扫 `deadline_at` 过期且未 ack 的消息，向发方信箱投 timeout 通知
- Mate 唤醒由 daemon 监听 `mate_messages` 插入事件触发（或短轮询）
- `get_mate_status` 从 daemon 内存中运行中的 runtime session 读取 frontier；非 running 状态从 DB 读
