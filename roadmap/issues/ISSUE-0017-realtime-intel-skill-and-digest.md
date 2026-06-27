---
id: ISSUE-0017
slug: realtime-intel-skill-and-digest
status: approved
milestone: M-06
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0017: Realtime Intel Skill and Scheduled Digest

对齐 Charter Phase Goal 第 5 项（研究员获得可解释分析）。实时信息跟进分两阶段：
先做按需 skill，等定时系统成熟后再做每日主动检查。

## Problem

用户问“现在 infra 有什么新进展”时，agent 需要按一套可复用规范查权威来源、
落档并生成报告。后续还需要 actor 定时运行同一套规范，每天检查是否有新消息。

## User-System Scenario

```text
阶段 1：按需
  用户问 infra 新进展
    → Agent 使用 realtime-intel skill
    → Skill 规定 source list、查询策略、wiki 落档格式和引用规范
    → Agent 搜索、读取、去重、总结、落档

阶段 2：定时
  研究员给 Actor 配置每日情报任务
    → Actor 每天按 skill 检查精选来源
    → 如有重要变化，更新 wiki/source archive
    → 给 conversation 留摘要或提醒用户
```

## Scope

- 定义 realtime-intel skill 的 wiki 规范：source list、查询策略、引用格式、
  落档位置、刷新规则。
- 第一版 source 优先官方/权威：arxiv、vendor blogs、GitHub releases、官方文档、
  精选 RSS。
- 定时阶段复用 ISSUE-0016 的 scheduled actor tasks。

## Out of Scope

- 第一版 Twitter/X 支持。
- 新闻推荐算法。
- 自动判断所有领域的重要性；第一版按 skill 的 source list 和规则执行。
