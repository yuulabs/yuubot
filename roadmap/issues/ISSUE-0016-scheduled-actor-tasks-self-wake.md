---
id: ISSUE-0016
slug: scheduled-actor-tasks-self-wake
status: approved
milestone: none
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0016: Scheduled Actor Tasks and Self Wake

对齐 Charter Phase Goal 第 5 项（overnight 运行实验，结果保留至研究员回来查阅）。
目标是让 actor 能安排未来任务，并在任务完成或到点后唤醒自己检查结果。

## Problem

overnight 实验和长期跟进不能依赖用户在线。agent 需要能创建定时任务、启动后台脚本，
并在完成后通过 ybot 通知对应 actor/conversation 继续处理。

## User-System Scenario

```text
研究员让 Agent 晚上跑一组实验
  → Agent 写运行脚本并启动后台任务
  → Agent 创建一个自唤醒任务：脚本完成后或明早 09:00 检查结果
  → 用户离开，conversation 没有新消息
  → 到点/完成后，System 唤醒 actor
  → Agent 读取结果、汇总失败/成功、留下报告
  → 研究员回来后能看到任务状态和结果
```

## Scope

- Actor 可创建 scheduled task。
- Actor 可创建 self-wake：未来某时间或某后台任务完成后，向指定 conversation
  注入唤醒事件。
- 后台任务状态可持久查看：scheduled / running / completed / failed。
- Admin UI 能看到 actor 的定时/后台任务概况。

## Out of Scope

- 分布式调度。
- K8S/GPU node 自动征用。
- 完整 workflow 引擎。
- 自动实时新闻；见 ISSUE-0017。
