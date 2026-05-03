# Issue 012: 静默的错误恢复——用户无法感知会话重置

**Severity**: Medium
**Source**: Code audit 2025-04-28

## 问题

当 Agent 执行出错时 (`mate/runner.py:206-211`)：

```python
if drive_result.status == "error":
    await self._close_lineage(lineage_id)
    _clear_task_id(workspace_path)
    lineage = _new_lineage()
    lineage_id = _lineage_id(lineage)
    task_id = _new_task_id()
```

系统静默地：
1. 关闭当前 agent（及其内核会话中的所有状态/变量）
2. 清除 lineage + task_id
3. 创建全新的 lineage

用户看到的是"新对话开始"，没有任何状态变更提示。旧的内核会话状态全部丢失。

## 风险

- 用户可能正在依赖之前会话中建立的变量/状态，突然丢失会造成困惑
- 无审计日志记录 lineage 关闭原因
- 无法区分"自然 TTL 过期"和"错误导致的强制重置"

## 待决策

- 是否应该通过 send_reply 通知用户会话已重置？
- 是否应在重置前保存 snapshot（内存快照）以便恢复？
- 错误重置和 TTL 过期是否需要不同的处理策略？
