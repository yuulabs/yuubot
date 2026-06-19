# RFC: Entity 可观测性

## 场景

用户让 agent 执行一个长时间运行的 bash 命令。完整调用流程：

```
1. 用户发消息 → agent.append_message(user_msg)
2. agent.call_llm() → LLM 思考中... (30s+)
3. LLM 返回 tool_call(bash, command="find / -name '*.log'")
4. agent.call_tools() → runtime.submit() → bash 执行
5. bash 执行中... stdout 不断输出 (60s+)
6. bash 结束 → tool result 写回 agent
7. agent.call_llm() → LLM 基于结果继续推理
8. LLM 返回最终回答
```

当前框架的问题：

- **步骤 2**：LLM 思考 30s+，前端完全静默。`call_llm()` 消费 stream 时，`ThinkingBlock`/`text` 被丢弃，只在 `llm.finished` 一次性发出。
- **步骤 5**：bash 不断输出 stdout，但框架用的是 `proc.communicate()` 一次性等完，进度不可见。BackgroundTask 用 `io.StringIO` 死缓冲，需要轮询。
- **步骤 1-8 任何时刻进程崩溃**：`TurnContext` 是批量模型，`end()` 才写入 span，崩溃则所有累积数据丢失。
- **前端回放**：如果用时间戳拼凑 agent 的 tool_call + bash 的 stdout + agent 的 tool_result，并行 tool call 导致顺序不确定，时间戳无法可靠重建因果链。

核心缺失：**当这些实体写入自己的输出时，框架无感知。** 现在需要一种方式让它们的产出变得可观测、可持久化、可回放。

---

## 核心设计

每个运行实体持有一个 **EntityLog**（append-only buffer）。实体写入 yuullm 原生类型，框架通过 PeriodicReporter 定期读出持久化 + 广播。

```
Entity → EntityLog.write(content)
              │
         PeriodicReporter 定期读增量
              │
         ├─→ EntityContext.flush(blocks)   # yuutrace（崩溃安全）
         └─→ eventbus.emit("output.chunk") # 实时（前端）
```

三个关键决策：

1. **内容用 yuullm 类型系统**：`str | ContentItem`，不另造。关联靠 `tool_call_id` 和 `parent_id`，不靠时间戳，不需要 role 字段。
2. **Block 是 tagged union**：metadata 类型决定变体，不用 `dict[str, Json]`。
3. **所有 OTEL span 立即 end**：`SimpleSpanProcessor` 立即导出，崩溃安全。
4. **Agent entity 生命周期绑定 Agent 实例**：`entity_id == agent_id`，同一个 Agent 的多轮输出进入同一个 entity。Agent 销毁/过期时写 `entity.end`。
5. **Tool 执行通过 ToolExecutionContext 传递可观测上下文**：executor 不再只拿 `UsageSink`，而是拿包含 sink、父实体、当前实体、tool_call_id 的 context。

---

## 场景回放：EntityLog 如何解决上述问题

用上面的 bash 调用场景，数据在 DB 中的形态：

```
entity       (agent_abc, type=agent, parent_id=)
entity.chunk (agent_abc, index=0, blocks=[
  ContentBlock(block_id=0, content="Let me think about this..."),
  ContentBlock(block_id=1, content={"type": "thinking", "thinking": "I need to search..."}),
  ContentBlock(block_id=2, content={"type": "tool_call", "id": "tc_1", "name": "bash", "arguments": "..."}),
])
entity       (bash_456, type=bash, parent_id=agent_abc, tool_call_id=tc_1)
entity.chunk (bash_456, index=0, blocks=[
  ProcessBlock(block_id=0, content="Finding log files...\n", stream="output"),
  ProcessBlock(block_id=1, content="/var/log/syslog\n", stream="output"),
  ProcessBlock(block_id=2, content="/var/log/auth.log\n", stream="output"),
])
entity.chunk (bash_456, index=1, blocks=[
  ProcessBlock(block_id=3, content="Permission denied: /root/...\n", stream="output"),
  ProcessBlock(block_id=4, content="/home/user/app.log\n", stream="output"),
])
entity.end   (bash_456, status=completed)
entity.chunk (agent_abc, index=1, blocks=[
  ContentBlock(block_id=3, content={"type": "tool_result", "tool_call_id": "tc_1", "content": "..."}),
])
entity.chunk (agent_abc, index=2, blocks=[
  ContentBlock(block_id=4, content="Based on the search results..."),
])
turn         (role=assistant, usage=..., cost=...)  ← 仅 LLM 元数据
```

Agent entity 绑定 Agent 实例，所以上面这轮结束后不写 `entity.end(agent_abc)`。只有 Agent 过期、销毁或 Stage 关闭时才写 agent 的结束标记。

前端回放：
- 按 `entity_id + chunk_index` 排序还原完整流
- `tc_1` 通过 `tool_call_id` 关联 call 和 result
- `parent_id=agent_abc` 关联 bash 子实体，bash entity 自身也记录 `tool_call_id=tc_1`
- **不靠时间戳拼接**

每个需求如何满足：

| 需求 | 机制 |
|------|------|
| LLM 思考时前端可见 | EntityLog.write → 0.5s 后 PeriodicReporter flush → EventBus → 前端 |
| bash 长时间运行可看进度 | EntityLog.write(stdout) → 同上；`tail()` 供主 agent 查看 |
| 已 flush chunk 崩溃不丢 | 每个 chunk span 立即 end → SimpleSpanProcessor 立即写 SQLite |
| 回放完整 trace | `entity_id + chunk_index` 排序，`tool_call_id` 关联，`parent_id` 递归 |
| 进度查看（主 agent 看子 agent） | `sub_entitylog.tail(2000)` 拿文本投影 |

---

## 一、EntityLog

Append-only buffer，存储 `str | ContentItem`（yuullm 原生类型，无扩展）。

```python
class EntityLog:
    _items: list[ContentLike]        # truth
    _subscribers: list[Callable]

    async def write(self, data) -> int          # 写入 + 通知
    def read_items(self, offset) -> (list, int)  # 增量读取
    def tail(self, max_chars) -> str             # 文本投影
    def subscribe(self, cb)                      # 订阅写入事件
```

- `str` 直接存储，不转 `TextItem`。
- `tail()` 对 str 裁剪，对 ContentItem 用 `render_item_text()` 投影。
- `write()` 返回 item offset，不分配 `block_id`。
- `block_id` 在 `PeriodicReporter.flush()` 构造 Block 时分配；如果 flush 合并多个相邻字符串，它们对应一个 Block 和一个 `block_id`。

---

## 二、EntityLogBlock — tagged union

```python
class ContentBlock(msgspec.Struct, tag="content"):
    block_id: int
    content: str | ContentItem           # yuullm 原生

class ProcessBlock(msgspec.Struct, tag="process"):
    block_id: int
    content: str                          # 进程输出只有文本，stdout/stderr 合并为终端视图
    stream: str = "output"                # 当前 bash 统一输出；未来需要时可扩展

class CommandBlock(msgspec.Struct, tag="command"):
    block_id: int
    content: str
    exit_code: int | None = None
    duration_s: float | None = None

EntityLogBlock = ContentBlock | ProcessBlock | CommandBlock
```

变体由数据来源决定：Agent 内容 → ContentBlock，bash 输出 → ProcessBlock，命令执行 → CommandBlock。未来扩展加新 Struct 即可。

---

## 三、OTEL 映射

所有 span 立即 end，`SimpleSpanProcessor` 立即导出：

| span 名 | 属性 | 用途 |
|----------|------|------|
| `entity` | id, type, parent_id, tool_call_id | 实体元数据，立即 end |
| `entity.chunk` | id, chunk.index, blocks (JSON) | 内容，约 0.5s 一个，立即 end |
| `entity.end` | id, status | 结束标记，立即 end |
| `turn` | role, usage, cost | LLM 元数据，无 items |

回放逻辑：`entity_id + chunk_index` 排序，`tool_call_id` 关联 call/result，`parent_id` 递归查找子实体。不靠时间戳。

---

## 四、Agent 写入

```python
# call_llm stream loop
case ThinkingBlock(tb):
    await entitylog.write(tb.to_message_item())
case Response(item=r):
    await entitylog.write(r.item)       # ContentItem 直接写入
case ToolCall(tc):
    await entitylog.write(yuullm.tool_call_item(tc))

# tool result
result = await execute_tool(tc)
await entitylog.write(yuullm.tool(tc.id, result).content[0])
```

不需要 role——`ToolCallItem.id` → `ToolResultItem.tool_call_id` 自带关联。

这里的 `ContentItem` 指 yuullm 已有类型系统中的消息 item。不要为 observability 新造 thinking/tool_call/tool_result dict schema。

Agent entity 生命周期：

- `entity_id` 使用 `agent_id`。
- entity 在 Agent 创建/首次启动时创建。
- 同一个 Agent 实例的多轮 LLM/tool 输出写入同一个 EntityLog。
- Agent 被 `agent.close()` / `expire_agent()` 移除或 Stage 关闭时写 `entity.end`。

---

## 五、ToolExecutionContext

Executor 需要拿到当前工具调用的可观测上下文，而不是只拿 `UsageSink`。

```python
class ToolExecutionContext:
    sink: UsageSink
    entity_id: str             # 当前工具 entity，例如 task_id 或 bash_xxx
    entity_type: str           # bash / python / ...
    entitylog: EntityLog
    parent_id: str             # 调用它的 agent entity
    tool_call_id: str
```

`Runtime.submit()` 负责创建 tool entity：

- `parent_id = agent_id`
- `tool_call_id = ToolCall.id`
- `entity_id` 优先使用 task_id 的稳定字符串，或按工具类型生成可读 id
- 创建 `EntityLog` 和 `PeriodicReporter`
- 将 `ToolExecutionContext` 传给 executor

工具执行结束时：

- final flush 当前 entity log
- 写 `entity.end(status=completed|error|cancelled)`
- tool result 仍由 Agent 写回自己的 EntityLog，用 `tool_call_id` 关联

---

## 六、PeriodicReporter

```python
class PeriodicReporter:
    log: EntityLog
    entity_ctx: EntityContext
    eventbus: EventBus
    entity_id: str
    entity_type: str
    parent_id: str
    tool_call_id: str | None
    block_factory: Callable    # ContentLike → EntityLogBlock

    async def start(interval=0.5)
    async def flush_final()     # 实体结束时最终 flush + end
    async def stop()
```

读 EntityLog 增量 → `_coalesce()` 合并相邻 str → `block_factory` 构造 Block → flush + emit。

不同实体的 `block_factory` 不同：Agent 产出 `ContentBlock`，Bash 产出 `ProcessBlock`。

`output.chunk` 事件直接发送和持久化 chunk 一致的数据结构，前端实时显示和 DB 回放使用同一套结构：

```python
{
    "entity_id": "bash_456",
    "entity_type": "bash",
    "parent_id": "agent_abc",
    "tool_call_id": "tc_1",
    "chunk_index": 0,
    "blocks": [...],
}
```

崩溃安全粒度：只保证已经被 PeriodicReporter flush 的 chunk 立即落库。默认 interval 约 0.5s，硬崩时最多丢失最后一次 flush 后仍在内存中的输出。如果需要写入即落盘，需要把持久化推进到 `EntityLog.write()`，不作为当前 MVP 默认要求。

---

## 七、TurnContext 改动

`add()` 保留，但不再管流式内容持久化。内容走 `EntityContext.flush(blocks)`。

TurnContext 只管 LLM 元数据：role、usage、cost、model。

---

## 八、yuullm：file:// URL 解析

图片走 URL 约定不变。新增 `resolve_image_url()`：`file://` → `data:` URL + LRU cache。各 provider 入口加一步 resolve。

这部分和 EntityLog 可观测性解耦，可以延后单独实现。

---

## 九、BackgroundTask

BackgroundTask 不再维护独立的 `io.StringIO` stdout 缓冲。后台任务的输出就是一个 EntityLog：

```python
class BackgroundTask:
    task: asyncio.Task[Any]
    log: EntityLog
    stdin_writer: Callable[[str], Any] | None = None
    closer: Callable[[], Any] | None = None
```

后台工具接口直接围绕这个模型设计：

- `check_background(task_id, max_chars=2000)` 返回 `bg.log.tail(max_chars)`。
- `write_background(task_id, data)` 只负责写 stdin，不进入输出日志。
- `close_background(task_id)` 关闭任务并 final flush。

不保留按字符 offset 增量读取。增量读取是 EntityLog/PeriodicReporter 的职责；agent 查看后台进度时只需要 recent tail。

---

## 十、不引入的东西

- **Process / ProcessRegistry**：各实体持有 EntityLog，生命周期由现有 Runtime/Actor 管理。
- **stdin/stdout/stderr 三通道**：EntityLog 是日志，不是 I/O 通道。Agent 输入走 `append_message()`。bash stdout/stderr 当前合并为终端视图。
- **ObservableBuffer 双视图**：EntityLog 只有 `list[ContentLike]`，`tail()` 按需投影。
- **`dict[str, Json]` metadata**：Block 变体是 tagged union。content 本身使用 yuullm 原生 item。

---

## 十一、迁移

1. 新增 `EntityLog` / `EntityLogBlock` / `PeriodicReporter`（`entitylog.py`）
2. yuutrace 新增 `EntityContext` + `ATTR_ENTITY_*`
3. EventBus 新增 `output.chunk`
4. Agent 加 `entitylog`，`call_llm()` 写入
5. Runtime 引入 `ToolExecutionContext`，executor 改为接收 context
6. Bash/Python 输出写入各自 tool entity 的 EntityLog
7. BackgroundTask `io.StringIO` → EntityLog，`check_background` 改为 `tail(max_chars)`
8. YuuTraceObserver 改造：创建 EntityContext + PeriodicReporter
9. yuullm `resolve_image_url()`（可延后）
10. 旧事件迁移，清理

---

## 十二、改动汇总

| 包 | 模块 | 改动 |
|---|---|---|
| yuuagents | `entitylog.py` (新) | EntityLog, EntityLogBlock, PeriodicReporter |
| yuuagents | `agent.py` | call_llm 写入 EntityLog |
| yuuagents | `runtime.py` | 创建 tool entity，传递 ToolExecutionContext |
| yuuagents | `tool_backends/base.py` | ToolExecutor.run 参数从 UsageSink 迁移为 ToolExecutionContext |
| yuuagents | `tool_backends/bash.py` | 流式输出写入 EntityLog |
| yuuagents | `python_session.py` | 同上 |
| yuuagents | `budget.py` | BackgroundTask stdout 缓冲改为 EntityLog |
| yuuagents | `observability.py` | YuuTraceObserver 创建 EntityContext + PeriodicReporter |
| yuuagents | `actor.py` | 创建 Agent 时创建 EntityLog |
| yuuagents | `eventbus.py` | 新增 output.chunk |
| yuutrace | `context.py` | 新增 EntityContext |
| yuutrace | `otel.py` | 新增 ATTR_ENTITY_* |
| yuullm | `_resolve.py` (新) | resolve_image_url()（可延后） |
| yuullm | providers | 入口 resolve file:// URL（可延后） |
