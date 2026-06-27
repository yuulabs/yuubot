---
id: ISSUE-0012
slug: file-upload-workspace-ingest
status: approved
milestone: M-03
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0012: File Upload and Workspace Ingest

对齐 Charter Phase Goal 第 1 项（Agent Infra 功能正常）与第 5 项（研究员给数据，
yuubot 分析并解释）。这是 researcher MVP 的第一块：用户能把资料交给 bot。

## Problem

经典场景里，研究员会先给 yuubot 一批文档、日志、论文、实验结果或截图，再让
agent 分析。目前 Conversation 缺少明确的文件上传入口和 prompt-visible 的文件
发现契约。即使 actor workspace 已有文件工具，用户仍没有自然路径把文件放进当前
对话/actor 的工作上下文。

## User-System Scenario

```text
研究员在 Admin Conversation 上传一个或多个文件
  → System 将文件落到该 actor/conversation 可访问的 workspace 区域
  → Conversation 界面显示已上传文件列表
  → Agent 下一轮 prompt 能看到这些文件的存在、路径、类型、大小和上传时间
  → Agent 可用现有文件读取工具读取内容
  → Agent 基于文件内容继续和研究员澄清、分析、产出结果
```

## Scope

- Admin Conversation 支持上传文件。
- 上传文件落盘到 actor workspace 下的稳定位置；路径必须 workspace-scoped。
- Agent prompt 明确列出当前可用上传文件，避免 agent 需要猜目录。
- 文件列表在 Conversation UI 可见。

## Out of Scope

- 复杂 RAG / embedding / chunk index。
- 多用户权限模型。
- 云对象存储。
- 非文本/图片格式的深度解析；第一版可以只保证文件可落盘、可发现、可读取。
