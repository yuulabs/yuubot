# Session 设计

yuubot 只有一种 session：**Chat Session** —— 用户与 bot 的多轮对话状态，维护在 Daemon 进程内存中。

长时间运行的任务（如编码）通过 `delegate` 工具委派给子 agent。子 agent 的进展通过 **OutputBuffer** 实时同步到父 agent，让父 agent 能看到子 agent 的 LLM 输出和工具调用。如果任务超过 `soft_timeout`，子 agent 会返回一个 handle，父 agent 可以用 `check_running_tool` 轮询或等待完成。

---

## Chat Session

Chat Session 让用户和 bot 进行多轮对话，而不需要每次都输入 `/yllm` 命令。

### 普通模式生命周期

1. **创建**：用户发送 `/yllm` 命令时创建，绑定到 `(ctx_id, agent_name)`。每个 ctx 同时只能有一个 chat session。
2. **延续**：在 TTL 内，后续消息自动归入同一 session，agent 能看到完整对话历史。
3. **过期**：最后一次活跃后 300 秒（5 分钟）无新消息则自动过期。
4. **主动关闭**：发送非 LLM 命令（如 `/bot`、`/help`）会立即关闭当前 session。
5. **Token 上限（Context Rollover）**：当某一轮 LLM 调用的 token 增量达到 `max_tokens` 阈值时，触发自动上下文滚动：
   - 旧 session 关闭；
   - 调用廉价摘要模型（默认 deepseek-chat）生成工作交接摘要（基于原始任务 + 最后 4 条消息）；
   - 创建新 session，摘要注入为 `handoff_note`；
   - 下一轮对话时，摘要以 `<上轮对话摘要>` 块注入任务文本，LLM 可以无缝继续。
   - 用户收到两条系统通知：压缩中 / 新会话就绪。

### 记忆整理（mem curator）

session 关闭时，若本次 session 足够实质性，则自动触发 `mem_curator` agent 在后台整理记忆。

- **触发时机**：TTL 自然过期（惰性，收到下一条消息时检测）、主动命令关闭、context rollover。三种关闭路径均受同一条件约束。
- **条件**：session 至少有 3 个 assistant 回合，且持续时间 ≥ 60 秒。过短的 session（问天气、算术等）不触发。
- **无副作用**：curator 作为 background task 运行，不阻塞当前消息处理，也不通知用户。

### Auto 模式（私聊专用）

MOD+ 用户可对私聊 ctx 开启 auto 模式（`/ybot on --auto`），行为与普通模式有以下不同：

| 维度 | 普通模式 | Auto 模式 |
|------|----------|-----------|
| TTL | 300s | 1800s（30min） |
| Session 过期后 | 需重新发 `/yllm` | 自动以当前 agent 重建 session |
| `/yllm#agent` | 切换并关闭旧 session | 切换 agent，旧 session 保留（供历史前缀缓存） |
| 非 LLM 命令 | 关闭 session | 执行命令，session 不受影响 |
| 同时存活 session 数 | 最多 1 个 | 每个 agent 一个，互不干扰 |

Auto 模式的 `/yllm` 语义变为 "选定/切换 agent"，而非 "开始会话"。第一条消息之前必须至少发送一次 `/yllm` 来选定 agent；此后 session 过期时会自动重建，无需再次发送。

关闭 auto 模式：`/ybot off`（同时关闭所有 session）。

Auto 模式状态持久化到 `auto_mode` 表（ctx_id + current_agent），Daemon 重启后自动恢复。Session 历史仍然是内存态，重启后丢失。

### 消息归入规则

不是所有消息都会归入 session，规则取决于聊天类型：

| 场景 | 是否归入 session |
|------|-----------------|
| 私聊，有活跃 session | 是 |
| 私聊 auto 模式，session 过期但有 current_agent | 是（自动重建） |
| 群聊，@bot，有活跃 session | 是 |
| 群聊，不 @bot，有活跃 session | 否（忽略） |
| 群聊，`/yllm` 命令 | 创建新 session |
| 普通模式，非 LLM 命令 | 关闭 session，执行命令 |
| Auto 模式，非 LLM 命令 | 执行命令，session 不关闭 |

关键点：**群聊中必须 @bot 才能延续 session**，普通群消息不会被归入。这避免了群里其他人的闲聊污染对话上下文。

### 实现

- `SessionManager`（`daemon/session.py`）：管理所有 chat session 的创建、查询、过期、关闭；维护 `_auto_ctxs` 和 `_current_agent`。
- `Dispatcher`（`daemon/dispatcher.py`）：在命令匹配前检查是否有活跃 session，决定消息走 session 延续还是新命令；auto 模式下实现 agent 切换和自动重建逻辑。

---

## 长时间任务处理（delegate + OutputBuffer）

对于耗时任务（如编码），使用 `delegate` 工具委派给专门的子 agent（如 coder）。

### 架构

```
Parent Agent ──delegate()──► Child Agent (coder)
       │                            │
       │    ◄── OutputBuffer ───   │
       │         (stream)           │
       │                            ▼
       │                  LLM streaming chunks
       │                  Tool call notifications
       │                  Tool output (via buffer)
       ▼
Parent sees real-time progress
```

### OutputBuffer 同步机制

1. **LLM 流式输出**：子 agent 的每个 LLM response chunk 实时写入 `output_buffer`
2. **工具调用通知**：子 agent 发起工具调用时，写入 `[calling {tool_name}]` 到 buffer
3. **子工具输出**：子 agent 调用的工具如果产生输出，通过该工具的 `current_output_buffer` 写入
4. **父 agent 读取**：父 agent 可以通过某种方式读取 buffer 内容（如通过 tool result 或特殊协议）

### Soft Timeout 处理

yuuagents 的 `ToolsContext.gather()` 支持 `soft_timeout` 参数：

- 如果子 agent 在 `soft_timeout` 内完成：正常返回最终结果
- 如果超过 `soft_timeout`：返回占位符（包含 handle），子 agent 继续在后台运行
- 父 agent 可以用 `check_running_tool(handle)` 轮询进展或等待完成

```python
# 伪代码示例
result = await delegate(agent="coder", task="implement feature X")
# 如果超时，result 可能是:
# "Still running (65s). handle=abc123\nTail output:\n[writing file...]"

# 后续轮询
final = await check_running_tool("abc123", wait=120)
```

### Silence Detection

Silence detection（长时间无消息提醒）只在 **root agent** 生效：

```python
# loop.py 中
if silence_interval_first > 0 and ctx.delegate_depth == 0:
    # 检测逻辑...
```

子 agent（delegate_depth > 0）不会触发 silence detection，因为：
1. 子 agent 的进展通过 OutputBuffer 实时同步给父 agent
2. 父 agent 可以向用户报告进展
3. 避免多层 agent 同时触发 silence 造成消息混乱

### Coder Agent 工作流

配置在 `yuuagents.config.yaml`。定位为**编码监督者**而非编码者：

1. **委派**：coder agent 分析任务，驱动 claude code 完成编码
2. **实时同步**：coder agent 的进展通过 OutputBuffer 实时同步给父 agent
3. **验收**：coder agent 检查产出，父 agent 也能看到过程
4. **反馈**：如果发现问题，父 agent 可以直接反馈或重新委派

使用 `{background_cli_prompt}` 变量注入 background CLI 使用说明，通过 `background run` 启动 claude code。

---

## 已移除的机制

以下机制已被移除，统一使用上述标准工具流：

- ~~`launch_agent` / `session_poll` / `session_interrupt` / `session_result` 工具~~：从未实际使用，delegate + OutputBuffer 提供更简洁的同步机制
- ~~yuubot 的 CTX_TIMEOUT~~：已移除。制造了孤儿任务，超时控制完全由 yuuagents 的 `check_running` tool 处理
- ~~yuubot 的 `_SessionManagerBridge`~~：不再需要，delegate 直接通过 `AgentRunner.delegate()` 运行
