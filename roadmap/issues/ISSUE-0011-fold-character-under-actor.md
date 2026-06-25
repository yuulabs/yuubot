---
id: ISSUE-0011
slug: fold-character-under-actor
status: in-progress
milestone: M-02
priority: P2
estimated_work_hours: 1
---

# ISSUE-0011: Fold Character under Actor

对齐 Charter Phase Goal 第 2 项（前端完成）。把 Character 从顶层独立 CRUD
收掉，折叠为 Actor 的 persona facet。研究员的心智里只有一个东西："一个
Actor = 一个有人格+能力+预算的说话对象"，character_prompt 是 Actor 的一个字段。

## Problem

前端把 Character 当作顶层 CRUD 暴露（`characters.tsx` / `characters.$id.tsx`），
研究员必须先理解"Character 是人格记录"，再去 Actor 页用 `default_character_id`
FK 引用它。但在数据模型上，Actor 已经内嵌 `default_character: CharacterRecord`
（`records.py:201`）——fold 在数据层已成立，拧巴只在前端单独暴露 Character
独立页。

同时，Character 在当前 UI 上只用到其 `system_prompt` 字段（其他如
`facade_module` / `default_hints` 是内部结构，无前端输入控件）。 researcher
看到的本质是"一个字符串字段"，却被迫为它维护一张独立的 CRUD 表 + FK 引用。

## User-System Scenario

```
研究员打开 Actor 列表 / Actor 新建表单
  → 新建 Actor 时，character_prompt 在 Actor 表单内直接填写
     （character_prompt 是该 Actor 的 System prompt 的第一 section；
       不再去 Character 页挑/建记录，不在 Actor 表单填 character_id FK）
    → 研究员保存 Actor
      → System 持久化 Actor，其 persona（character_prompt）随 Actor 而定
        → 系统于后台自行维护或派生对应的 Character 记录（实现细节，前端不可见）

研究员打开已有 Actor 的详情页
  → 可编辑该 Actor 的 character_prompt 字段
    → 保存即生效（无需跳转 Character 页，无需理解 Character 概念）

Character 独立 CRUD 页（characters.tsx / characters.$id.tsx）
  → 移除（研究员前端不再有"Character"这个顶层导航/页面）
```

## Scope (lazy: 契约只列用户观测点，不复述实现)

- **character_prompt 作为 Actor 的字段在前端暴露**：新建/编辑 Actor 表单
  含 character_prompt 输入。该字段语义是"该 Actor 的 System prompt 的第一
  section"——这是用户可见的语义，不是后端 schema 字段名约束（后端
  `CharacterRecord.system_prompt` 是实现细节）。
- **Character 顶层 CRUD 页移除**：前端导航与路由不再暴露 Character 独立页。
- 后端 `CharacterRecord` 是否保留为内部结构、Actor 创建路径是(a)前端双调（先
  建 Character 再建 Actor 引用）还是(b)后端接受内联 character_prompt 自动
  派生 Character —— **均是实现决策，契约不约束**。用户只观测"在 Actor 表单
  填 character_prompt，不用碰 Character 页"。

## Out of Scope

- per-conversation character override 能力（`conversations.py:545,552` ——
  Conversation 首次 send 时可指定非 Actor 默认的 `character_id`）—— 既有
  后端路径，不在本 Issue 触及。是否在前端暴露此 override 非 Issue 契约。
- CharacterRecord 的 `facade_module` / `default_hints` / `name` /
  `description` 等字段的现实意义/迁移 —— 实现细节，不约束。
- CharacterRecord 的数据迁移或 schema 变更 —— 若 YuuDev 判定需要，是实现决策。

## 衔接

ISSUE-0007 的"全部 CRUD 页统一基线"列表含 `characters`
（ISSUE-0007 scenario 第 48 行 `characters`）。本 Issue 落地后 characters
页消失——非冲突，是 0007 的基线目标页集合缩减一项。建议 0011 先于或协同
0007 最终落地，避免给一个即将被删的页做基线返工。
