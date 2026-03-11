# 测试设计

## 目标

yuubot 的测试只验证 **用户能感知的产品行为**，不验证 yuuagents / yuullm / yuutrace 的内部实现。

一句话说清楚：测试要回答“这个 QQ bot 对用户是不是按需求工作”，而不是“某个内部类今天还是不是这个 shape”。

## 应该测什么

### 1. 消息路由

- 群聊默认需要 `@bot`
- free 模式下，群聊里的 `/yllm` 不需要 `@bot`
- bot 关闭后，普通用户消息应被忽略
- Master 私聊永远可用
- 非白名单用户私聊默认被忽略

### 2. 命令权限

- Folk 不能执行 `/bot ...`
- Master 可以授权、开关 bot、允许私聊
- master-only agent 只能被 Master 选中

### 3. 会话行为

- `/yllm` 会创建 session
- 满足归入规则的后续消息会延续同一 session
- 非 LLM 命令会关闭普通 session
- `/yclose` 会显式关闭 session

### 4. Agent 真实链路

当测试声称“agent 回复了用户”时，必须看到至少一个真实外部效果：

- session 中出现了新的 assistant 内容；或
- recorder API 收到了 `send_msg` 请求

如果要声称“工具调用链路可用”，则必须看到 recorder API 收到了由 `ybot im send` 触发的真实请求。只检查 history 长度不算。

### 5. Soft Timeout 体验

soft timeout 是产品需求。测试要验证：

- 长任务不会让 bot 长时间无响应
- 超时后会返回用户后续可追踪的结果，例如 `still running` 和 `handle=...`
- 后续轮询时，用户能先看到已有进度，再拿到最终结果

这类测试可以下沉到 `Dispatcher / AgentRunner + 公开 tool 接口` 这一层，但不应直接断言上游库的私有状态。

## 不应该测什么

下面这些不属于 yuubot 的仓内行为测试：

- `OutputBuffer` 的字节累积细节
- `RunningToolRegistry` 的内部状态机
- `ToolsContext.gather()` 的 soft timeout 细节
- 任何直接断言上游库私有字段的测试

这些如果需要，应放到对应上游仓库里测。

## 写法约束

- 断言面向需求，不面向函数内部步骤
- 不允许”只有注释，没有断言”的测试
- 不允许”只要不抛异常就算通过”的测试
- 对外部依赖优先使用最薄的假实现，只替换网络边界，不伪造 yuubot 自己的核心流程
- 需要证明工具链真的执行时，优先断言 recorder API 实际收到请求

## 已知问题与修复

### Session Continuation Race Condition (Fixed 2026-03-11)

**症状：** 用户在 agent 完成上一轮响应时发送续接消息，消息被 ping 到运行中的 flow 但从未被处理。Session 看起来卡住，直到 `/yclose`。

**根本原因：** `yuuagents/loop.py` 中的竞态条件：

1. Agent 正在执行**最后一次 LLM 调用**（纯文本响应，即将退出）
2. 在 `await agent.llm.stream(...)` 期间，用户的新消息到达 → dispatcher ping 运行中的 flow
3. LLM 完成 → 无工具调用 → `_step` 标记 `agent.status = DONE` 并返回 `[]`
4. 主循环检查 `not agent.done()` → False → 跳过 children-ping 块
5. `while not agent.done()` 退出 → `_active_flows.pop(ctx_id)` 清理
6. **Ping 永远留在 `root_flow._ping_queue` 中，从未被排空**

`_drain_pings()` 仅在 `tool_calls` 非空时运行。当最后一步是纯文本时，不会排空。

**修复：** 在 `_step` 返回 DONE 且无工具调用后，排空任何待处理的 ping 并将其注入为系统消息，保持 agent 活跃以处理它们。

```python
# 在 yuuagents/loop.py 中，_step 返回后：
if not tool_calls and agent.done():
    pending_pings = _drain_pings(root_flow)
    if pending_pings:
        lines = [format_ping(p, flow_manager) for p in pending_pings]
        summary = “[system] 后台通知:\n” + “\n”.join(lines)
        agent.history.append(yuullm.user(summary))
        chat.user(summary)
        agent.status = AgentStatus.RUNNING  # 保持循环活跃
```

**提交：** `yuuagents@632cdcd` (2026-03-11)

**复现步骤：**
1. 使用慢查询（如搜索漫画章节）启动 session
2. 当 agent 生成最终文本响应时，发送续接消息
3. 无修复：消息丢失，session 看起来卡住
4. 有修复：消息被排空并在下一次循环迭代中处理

**测试覆盖：** 在 `tests/flows/test_llm_session.py` 中添加集成测试，模拟此竞态条件。
