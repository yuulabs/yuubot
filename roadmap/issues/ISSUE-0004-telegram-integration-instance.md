---
id: ISSUE-0004
slug: telegram-integration-instance
status: approved
milestone: M-05
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0004: Telegram Integration Instance

非核心 plumbing。Scenario 契约引用 ISSUE-0002，本文只记 Telegram 平台差异。

## User-System Scenario

引用 ISSUE-0002 全链路契约（ingress → gateway → actor → response）。差异：

```
Telegram user sends message via Telegram client
  → Telegram Bot API (getUpdates polling or webhook)
    → Telegram Integration receives event, normalizes to yuubot payload
      → [ISSUE-0002 框架接管]
        → agent reply → response() → Telegram Bot API → user 看到回复
```

## Platform Differences vs ISSUE-0002

- **协议接入**：Telegram Bot API（`python-telegram-bot` 或直接 HTTP）。
- **消息格式**：Telegram markdown / media / inline keyboard payload 结构。
- **生命周期**：long polling 或 webhook；Bot token 配置在 secrets（不入库）。

## Out of Scope

- Telegram Bot 注册 / token 申请 = 用户运维任务，非 Issue 范围。
- Telegram channel / group 多端路由策略 = 未来 Issue，不在本 instance。
