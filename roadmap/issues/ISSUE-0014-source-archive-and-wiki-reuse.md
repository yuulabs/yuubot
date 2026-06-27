---
id: ISSUE-0014
slug: source-archive-and-wiki-reuse
status: approved
milestone: none
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0014: Source Archive and Wiki Reuse

对齐 Charter Phase Goal 第 1 项（Agent Infra）与第 5 项（可观测性/可解释性）。
目标是让 agent 查过的资料可持久复用，而不是每次清空上下文后重新从零搜索。

## Problem

实时信息跟进不能只停留在一次性搜索。agent 读取并引用过的来源应当落档：
后续相似问题先查本地 archive/wiki，再决定是否刷新外部来源。这样能减少重复查询，
也能让结论有时间线和来源链。

## User-System Scenario

```text
Agent 通过 web.read 读取一篇 NVIDIA blog / arxiv paper / GitHub release
  → System 保存 source record：URL、title、source、抓取时间、发布时间、hash、摘要
  → Agent 在 workspace/wiki 中写入 topic note，记录本次结论和来源链
  → 用户过几天再次问类似问题
    → Agent 清空上下文后仍能发现已有 archive/wiki
    → Agent 先复用旧资料，再判断是否需要上网刷新
```

## Scope

- 定义 source archive 的落盘位置和最小记录格式。
- 定义 wiki/topic note 约定：主题、结论、引用、更新时间。
- prompt 告诉 agent：回答实时/研究类问题前，先检查本地 archive/wiki。
- 支持过期刷新：旧来源不直接覆盖，保留时间线。

## Out of Scope

- 自动知识图谱。
- embedding 检索系统。
- 多 actor 共享知识库的权限/冲突处理。
- 自动删除旧档案。
