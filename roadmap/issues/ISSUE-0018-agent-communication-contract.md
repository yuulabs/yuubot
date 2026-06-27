---
id: ISSUE-0018
slug: agent-communication-contract
status: approved
milestone: none
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0018: Agent Communication Contract

对齐 Charter Phase Goal 第 5 项（可解释性）。目标是降低用户心智负担，让 agent
把混乱留在内部，把正确下一步交给用户。

## Problem

一种常见反模式是：agent 先解释当前系统如何混乱，再列出多个方案让用户拍板。
这会把实现细节和错误选项转嫁给用户。yuubot 的默认沟通范式应当是：agent 管理
细节，只在真正需要用户意图、风险偏好或外部约束时提问。

## User-System Scenario

```text
用户提出一个模糊但可推进的问题
  → Agent 先给出推荐的正确下一步
  → Agent 自行处理可判断的实现细节
  → 若必须提问，先说明为什么该信息会改变结果
  → Agent 不把多个明显劣质方案丢给用户筛选
  → 用户看到的是清晰路径，而不是内部混乱
```

## Scope

- 在默认 actor/system prompt 或 researcher skill 中加入沟通契约。
- 明确提问边界：只有用户意图、风险偏好、外部约束会改变结果时才问。
- 明确输出边界：推荐方案优先，必要取舍简明说明。

## Out of Scope

- 强制所有第三方 skills 遵守。
- 自动评分系统。
- UI 文案大改。
