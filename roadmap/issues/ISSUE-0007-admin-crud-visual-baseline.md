---
id: ISSUE-0007
slug: admin-crud-visual-baseline
status: in-progress
milestone: M-02
priority: P1
estimated_work_hours: 8
---

# ISSUE-0007: Admin CRUD Visual Baseline

对齐 Charter Phase Goal 第 2 项（前端完成）。推进 M-02 stopping point 第 (3) 项。

## Problem

Admin 的 CRUD 页面风格 ad-hoc，每页手搓，无统一基线。

recon 确认：

- **shadcn/ui 组件库已就位**，但每个页面对布局/空态/PageShell 各自手搓：
  - `actors.tsx` / `characters.tsx` / `capability-sets.tsx` / `routes.tsx`：
    左 Table + 右 create form 模式；
  - `providers.tsx`：全宽 Table + dialog create form；
  - `integrations.tsx`：card grid；
  - `admin.conversations.tsx`：sidebar list + detail panel — **与其他页结构
    完全不同**。
- `PageShell` / `Empty` helper 在多页重复定义（如 `actors.tsx:306-336` vs
  `characters.tsx:216-226`）。
- `<DataTable>` 组件已存在于 `components/data-table.tsx` 但**全仓无人用**。

研究员明确要求：先做 demo，review 迭代至 approve，再照 approve 版落地到所有
CRUD 页。这是本 Issue 的 stopping point。

## User-System Scenario

```
研究员打开 CRUD 视觉 demo（YuuDev 产出，独立于现有页面或独立 route）
  → 看到统一的布局基线：PageShell（统一 header / empty 态 / action 布局）、
    DataTable（如适用）、表单/详情页风格一致
  → 研究员 review，指出不满意处
    → YuuDev 迭代 demo
      → 研究员再次 review
        → … 直到研究员 approve

研究员 approve 后
  → YuuDev 将 approve 版基线落地到所有 CRUD 页
    （actors / characters / capability-sets / routes / providers /
     integrations / conversations）
  → 研究员打开任一 CRUD 页 → 视觉风格一致、动作布局一致、空态一致
  → 若某页因自身特性（如 conversations 的侧栏 + 详情）需偏离基线，
    偏离处对研究员可见且已被 approve（不是悄悄 ad-hoc）
```

## Scope (lazy: 契约只列用户观测点，不复述实现)

- **Demo-first**：先产出统一视觉 demo，研究员 review 迭代至 approve。
  demo 的具体设计（布局/间距/配色/组件选型）是 YuuDev 设计决策，本 Issue 不
  约束 — 用户只观测 "我看到了 demo，我 approve 了"。
- **落地到全部 CRUD 页**：approve 后照搬基线到所有现存 CRUD route。
- **复用既有组件**：`<DataTable>` 已存在但未用，落地时复用而非再造。

## Out of Scope

- Conversation 聊天视图本身（流式消息渲染 / tool renderer）的视觉重设计 —
  非本 Issue，cruds 列表 + 详情壳子参与基线即可。
- 新增组件库 / 设计系统迁移 — 维持 shadcn/ui。
- i18n / 主题切换 / 暗色模式 — 非本 Issue。
