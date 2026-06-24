---
id: ISSUE-0010
slug: conversation-entry-via-actor
status: approved
milestone: M-02
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0010: Conversation Entry via Actor

对齐 Charter Phase Goal 第 2 项（前端完成）。收敛 Conversation 的创建入口，
让"与某个 Actor 展开会话"成为主路径，而不是"先进 Conversation 这个顶层概念、
再在里面挑 Actor"。

心智模型：Actor 是持久参与者（"跟谁说话"），Conversation 是与其的一次会话
（一个线程）。研究员从 Actor 处发起会话，而非从 Conversation 顶层新建后再
回填 Actor。

## Problem

前端把 `Character / CapabilitySet / Actor / Conversation` 当作四个并列顶层
CRUD 暴露，Conversation 的创建主入口是：

- 顶部 nav「Admin Conversation」→ 列表页右上 `[New Conversation]`
  （`admin.conversations.tsx:61`）→ 跳草稿页 `/admin/conversations/new` →
  草稿页内 `<Select>` 让研究员先选 Actor
  （`admin.conversations.$conversationId.tsx:929-930`）→ 发消息。

这条路径让研究员被迫先理解"Conversation 是一个独立顶层实体"，再在里面
挑 Actor。Actor 列表行里其实已有一个「Conversation」次入口
（`actor-actions.tsx:14`，链接到 `actor-${actor.id}` 草稿，Actor 已预选），
但它只是个 ghost 小按钮，不是主流程。

与产品语义拧巴：后端语义本就是"Actor 是参与者，Conversation 是与其的会话"
（`design/checklist.md:93-96,113` — conversation-mode agent 线程、不进
mailbox、面向一个明确的 Agent 线程）。前端应反映此语义：从 Actor 处发起会话。

## User-System Scenario

```
研究员打开 Actor 列表 / Actor 详情页
  → 在某 Actor 上点「与该 Actor 对话」动作
    → System 进入该 Actor 的新会话草稿页（Actor 已绑定、不可改选）
      → 研究员发第一条消息
        → System 持久化 Conversation（绑定该 Actor）
          + 启动 conversation-mode agent 线程（独立 history/workspace/SSE，
            不进 Actor mailbox —— 既有行为，不变）
          → 研究员看到流式回复，会话建立

研究员想继续某条旧会话
  → 在该 Actor 详情页看到其历史会话列表 → 点开 → 续会话

全局 Conversation 列表保留为管理面（summary / 单删 / 批量删，见 ISSUE-0006），
不作为"创建新会话"的主入口

旧的「Conversation 列表顶部 [New Conversation] → 草稿内 <Select> 选 Actor」
  → "选 Actor" 步骤移除；顶层 New Conversation 按钮降级或移除，
    不再作为创建会话的主路径
```

## Scope (lazy: 契约只列用户观测点，不复述实现)

- **Actor 处的发起动作**：Actor 列表行 / Actor 详情页提供"与该 Actor 对话"
  入口。具体文案（"对话"/"Start conversation"/"New chat"）与按钮位是
  YuuDev 设计决策，契约只约束"入口在 Actor 处"。
- **草稿页 Actor 锁定**：从 Actor 进入的新会话草稿，Actor 字段不可改选。
  是否去掉 `<Select>` 组件还是只 disable，是 YuuDev 实现。
- **Actor 详情页历史会话列表**：Actor 详情页列出该 Actor 的历史会话，可点开
  续会话。需要后端 `GET /actors/{id}/conversations` 或复用现有 list 过滤
  ——是否新增端点是 YuuDev 实现，用户只观测"能在 Actor 处看到其会话历史"。
- **旧入口处理**：顶层 `[New Conversation]` 按钮降级或移除，草稿页内选 Actor
  步骤移除。是降级还是移除是 YuuDev 实现。

## Out of Scope

- Conversation 聊天视图本身的视觉重设计（流式渲染 / tool renderer）——
  非 Issue，归 ISSUE-0007 的 CRUD 视觉基线。
- Conversation 的新会话草稿是否复用 `actor-${actor.id}` 现有约定 ——
  实现细节，非用户契约。

## 衔接

ISSUE-0005 scenario 末尾"研究员点「New Conversation」→ 选该默认 Actor"的叙述
会因本 Issue 过时——0005 的契约（填 API key → 默认记录自动 provision → 不
碰四张表即可跑通）不变，仅入口叙述需 amend 为"进入默认 Actor → 与其对话"。
ISSUE-0010 approved 落地后，作为 trivial 文字编辑 amend 0005，不走 ISSUE-CHANGE。

ISSUE-0007 的"全部 CRUD 页统一基线"含 conversations 列表 + 详情壳子——
本 Issue 的入口变更是概念面收敛，与 0007 的视觉基线是两件事，协同落地
以避免返工。
