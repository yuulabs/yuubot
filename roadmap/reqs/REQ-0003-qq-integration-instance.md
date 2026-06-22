---
id: REQ-0003
slug: qq-integration-instance
status: approved
derived_from: sprint.md#current-milestone
estimated_work_hours: 1.5
---

# REQ-0003: QQ Integration Instance

非核心 plumbing。Scenario 契约引用 REQ-0002,本文只记 QQ 平台差异。

## User-System Scenario

引用 REQ-0002 全链路契约(ingress → gateway → actor → response)。差异:

```
QQ user sends message via QQ client
  → NapCat WS (127.0.0.1:8766 per config.yaml recorder.relay_ws)
    → QQ Integration receives event, normalizes to yuubot payload
      → [REQ-0002 框架接管]
        → agent reply → response() → NapCat WS → QQ user 看到回复
```

## Platform Differences vs REQ-0002

- **协议接入**:NapCat WebSocket(历史实现已删,需重写)。不走官方 Bot API。
- **消息格式**:QQ 富媒体(图片 / @ / 引用)的 payload 结构。
- **生命周期**:NapCat 连接断开 → integration 重连(不持久 Actor)。

## Out of Scope

- NapCat 部署 / 配置文档 = 运维任务,非 REQ 范围。
- QQ 群消息分发策略 = 未来 REQ,不在本 instance。
