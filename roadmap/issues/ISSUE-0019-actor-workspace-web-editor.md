---
id: ISSUE-0019
slug: actor-workspace-web-editor
status: approved
milestone: none
priority: P3
estimated_work_hours: unknown
---

# ISSUE-0019: Actor Workspace Web Editor

对齐 Charter Phase Goal 第 2 项（前端完成）与第 1 项（Agent Infra 扩展点）。这是
Actor workspace 的增强功能：在 Admin 中提供类似 VS Code Web 的在线文件树和文件
编辑能力。Skill 修改只是它自然带来的一个副产物，不把本 Issue 定义成专门的 skill
管理器。

## Problem

Actor workspace 是 agent 的真实工作区，但当前研究员缺少一个轻量的在线入口查看
文件树、打开文件、修改文本文件。文件上传、脚本、实验结果、wiki/source archive、
`.agents/skills/` 都会落在 workspace 里；没有 web editor 时，用户只能通过本机
文件系统或让 agent 代为查看，反馈路径太绕。

本 Issue 不重新发明 IDE。目标只是提供在线编辑和文件树浏览能力，让用户能直接检查
和修正当前 actor workspace 中的文件。

## User-System Scenario

```text
研究员打开某个 Actor 的 workspace
  → 浏览 actor workspace
  → 在文件树中打开一个文本文件
  → 在线编辑并保存
  → 新建/重命名/删除文件或目录
  → 查看实验脚本、结果、wiki/source archive、.agents/skills/SKILL.md 等文件
  → 回到 Conversation，让 agent 基于修改后的 workspace 继续工作
```

## Scope

- Actor 页面增加 Workspace 入口。
- 提供 workspace-scoped 文件树浏览。
- 支持文本文件打开、编辑、保存。
- 支持新建/重命名/删除文件和目录。
- 支持基础文件状态：大小、修改时间、是否二进制/不可编辑。
- 编辑器可使用现成 web editor 组件；本 Issue 不自研编辑器核心。

## Out of Scope

- 重新实现 VS Code / LSP / debugger / terminal。
- 多人协作编辑。
- Git 版本管理 UI。
- notebook 体验。
- 专门的 skill registry / skill dependency 管理；`.agents/skills/` 只是普通
  workspace 目录。
