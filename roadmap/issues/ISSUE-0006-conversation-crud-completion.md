---
id: ISSUE-0006
slug: conversation-crud-completion
status: approved
milestone: M-02
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0006: Conversation CRUD Completion

对齐 Charter Phase Goal 第 2 项（前端完成）。推进 M-02 stopping point 第 (2) 项。

## Problem

Conversation 的 CRUD 严重残缺，且列表纯靠裸 UUID 分辨对话，研究员无法辨识。

recon 确认：

1. **无单删、无批量删、后端连 DELETE 端点都没有**。Conversation 是唯一一个
   既无前端删除按钮、后端也无对应路由的实体 — 其他资源
   （Actor/Character/CapabilitySet/Routes/Providers）都有 Trash2 单删，走
   `commands/_app.py:73` 的通用资源 DELETE `/{resource_type}/{id}`，但
   conversations 不在资源表之列，无 DELETE 路径。
2. **`title` 字段早已存在于 `ConversationRecord` 且已持久化**（`records.py:234-248`
   `title: str = ""`，`ConversationORM` 的 `title text()` 列已建），但：
   - 从未被填充 — `_create_first_send_conversation`（`conversations.py:534`，
     第 574 行）永远传 `title=""`；
   - 不在 API 响应里 — `_conversation_metadata()`（`handlers.py:96-106`）和
     `list_conversations`（`handlers.py:380-388`）都省略它；
   - 前端类型 `ConversationListItem` / `ConversationData`（`types/api.ts:319-328,
     351-356`）不带 `title`。
   列表项只显示 `conversation_id`（裸 UUID）+ `updated_at`/`actor_id`。
3. **全 Admin 无任何 bulk 模式**。`<DataTable>` 组件已存在（`components/
   data-table.tsx`）但全仓无人用；`<Table>` 的 `[role=checkbox]` CSS 已有但无页接线。

## User-System Scenario

```
研究员在 Conversation 列表
  → 每条显示 summary（ConversationRecord.title，首轮后由系统生成）而非裸 UUID
  → 勾选多条对话 → 点「批量删除」
    → System 在一个事务内级联删除：Conversation + 其下所有 messages +
      history items
    → 列表刷新，被删条目消失
  → 或在单条上点删除 → 该条 Conversation + 子记录级联清除
    → 列表刷新，该条消失

研究员与新 Conversation 跑完第一轮
  → Agent 回复后
    → System 用轻量小模型（config.yaml `llm_roles.summarizer`，
      当前指向 deepseek-v4-flash）对首轮对话内容生成一段短 summary
    → System 持久化写入该 Conversation 的 title 字段
    → 列表页该条从裸 UUID 变为 summary 文本
```

## Scope (lazy: 契约只列用户观测点，不复述实现)

- **后端 DELETE Conversation**：补端点 + 级联清 `conversation_messages` /
  `conversation_history_items`（FK 由 `conversation_id` 承载）。批量删走
  单次请求多 id 还是循环单删是实现选择，本 Issue 不约束 — 用户只观测"勾选多条
  → 一次动作 → 都消失"。
- **Summary 生成**：首轮 Agent 回复后，系统用 `llm_roles.summarizer` 指向的
  轻量小模型生成短 summary，写入已存在但空的 `title` 字段。recon 已确认多条
  候选插入点（`_on_runtime_event` / `_handle_llm_finished` / `send_message`
  首轮路径 / `_summarize_agent_history`），具体落哪个是 YuuDev 实现。
- **API 序列化 `title`**：`list_conversations` / `_conversation_metadata` 把
  `title` 带出去；前端类型带上 `title`；列表显示 `title`，回退 `conversation_id`。
- **前端批量删 + 单删 UI**：列表行加 checkbox + 批量动作 bar；单条加删除按钮。

## Out of Scope

- summary 的具体模型 / prompt / 长度上限 — 实现决策，非用户契约。
- Conversation 重命名（研究员手动改 title）— 非本 Issue，title 是系统生成。
- Conversation 归档 / 收藏 / 搜索过滤增强 — 非本 Issue。
