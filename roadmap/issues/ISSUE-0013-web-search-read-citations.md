---
id: ISSUE-0013
slug: web-search-read-citations
status: approved
milestone: none
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0013: Web Search and Read with Citations

对齐 Charter Phase Goal 第 1 项（Agent Infra 扩展点稳定）与第 5 项（研究员获得
可解释分析）。这是 researcher MVP 的第二块：agent 能主动查外部资料，而不是靠
过期模型知识。

## Problem

用户问实时问题时，agent 需要搜索和读取外部信息源。当前缺少 builtin
`web.search` / `web.read` 能力，agent 无法稳定获取网页、论文或官方博客内容，也
无法以结构化方式保留引用信息。

## User-System Scenario

```text
研究员问：最近 infra 有什么新进展？
  → Agent 调 web.search 查询 arxiv / 官方博客 / 文档 / 普通网页
  → System 返回结构化搜索结果：title、url、source、published_at/updated_at（如有）
  → Agent 选择结果调 web.read
  → System 返回正文/摘要 + 抓取时间 + canonical URL
  → Agent 输出分析结论，并在结论旁保留来源引用
```

## Scope

- 提供 builtin web search capability。
- 提供 builtin web read capability。
- 工具返回结构化 citation metadata：URL、title、source、抓取时间、可得的发布/
  更新时间。
- Agent 的最终回答能引用读取过的来源。

## Out of Scope

- Twitter/X 第一版支持；API/反爬成本高，后续按需做。
- 全网爬虫。
- 自动事实校验系统。
- source 持久化复用；见 ISSUE-0014。
