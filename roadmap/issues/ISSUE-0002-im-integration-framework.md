---
id: ISSUE-0002
slug: im-integration-framework
status: approved
milestone: M-05
priority: P1
estimated_work_hours: 5
---

# ISSUE-0002: IM Integration Framework

对齐 Charter Phase Goal 第 3 项（集成框架稳定起点）。

## Problem

当前只有 `echo` / `github` / `test_im` 三个 integration 实现，且 `test_im`
只是测试桩。没有真实的 IM 集成，没有验证过"外部 IM 消息进入 → Actor 处理 →
回复原路返回"的完整链路契约。本 Issue 定义稳定的 IM 集成模板，使 QQ /
Telegram（ISSUE-0003 / ISSUE-0004）能照模板快速接入。

## User-System Scenario

```
External IM user sends message (via QQ or Telegram)
  → System IM Integration receives event
    → Gateway.ingest() routes accepted event into Conversation
      → Actor mailbox receives message
        → Agent loop processes → produces assistant reply
          → Agent calls IM Integration response() (原路回复)
            → System delivers reply back to the IM user
  → IM conversation is stateless across daemon restarts:
      Conversation + expire policy (no persistent Actor)
      (符合宪章层决策：IM 场景不必持久 Actor；持久 Actor 是 overnight
       实验场景的需求，不属于本 Issue)
```

## Scope (lazy: 契约边界，不复述实现)

稳定 IM 集成模板涵盖四段链路：

1. **Ingress** — IM 平台事件（WS / webhook / polling）→ yuubot 事件 payload。
2. **Gateway routing** — `Gateway.ingest()` 接受事件 → `RouteTable` 映射到
   `ConversationRoute`（integration / character / actor）。不做 workflow 分支。
3. **Actor handling** — `system_ingress.send()` 或 mailbox 路径，.Actor
   处理消息，Agent loop 产出回复。
4. **Response** — Actor 通过 facade 调用 `IntegrationInstance.response()`，
   原路回复。语义是即时回复，不做延迟/定时（架构宪章约束）。

模板要求：
- IM 场景走 `Conversation + 过期 expire`，无持久 Actor（宪章层决策落点）。
- 一个 `IntegrationFactory` + `IntegrationInstance` 实现，既有 ingress 又有
  response，是完整闭环（对照 `echo.py` 模板，但 echo 无真实 ingress）。
- 第三方集成的接入路径（manifest.yaml + 子进程）应能支持 IM 类集成，不强制
  IM 必须内置。

## Prompt Transparency Principle 落点

IM 相关事件（消息到达、投递失败）是否需要进 agent prompt？
- 消息到达 → agent 处理 → 自然进 prompt（是 agent 的输入）。
- 投递失败：/ 网络断开 → 影响后续 agent 行为吗？
  - 若 agent 调 `response()` 失败 → agent 应知道（否则它以为回复发出去了）。
  - 因此 `response()` 的失败结果要能在 prompt 层被 agent 观测到。

## Out of Scope

- 具体协议库选型（QQ / Telegram 协议库）= ISSUE-0003 / ISSUE-0004 的工作。
- 持久 Actor / overnight 消息保留 = 宪章第 5 项，不在本 sprint。
- IM 多端同步 / 消息去重 / 离线消息队列 = 非本 sprint，未来按需加。
- 容错（YNetwork 相关）ISSUE-CHANGE 时再议。
