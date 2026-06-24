# yuubot Milestones

> 技术停止点的 chron 列表。top → bottom 读 `completed → WIP → draft`。
> 同时至多一个 `WIP`。`completed` 的判据：其 P1 Issue 全部 `implemented`。

## M-01 — Agent Runs Right + IM Reaches Actor

- **Status**: `WIP`
- **Builds**: 对齐 Charter Phase Goal 第 1 项（Agent Infra 功能正常）+ 第 3 项
  （集成框架稳定起点）。研究员可以：启动 yuubot → 在 Admin Conversation 让
  Agent 在 workspace 里跑 Python（venv 隔离、预装数据科学包、自检可用包、可装新包、
  可重启 kernel）→ 通过 QQ 或 Telegram 给 Actor 发消息 → Actor 回复。
- **Stopping point**: 至少一个 IM 发消息 → Actor 回复。两个 IM 都做完是 sprint 内延伸，
  不单列验收门槛。
- **Links**: ISSUE-0001, ISSUE-0002（核心 P1）；ISSUE-0003, ISSUE-0004（plumbing P2）。

## M-02 — Admin UX Equals Backend Surface

- **Status**: `draft`
- **Builds**: 对齐 Charter Phase Goal 第 2 项（前端完成）。让 Admin UI 的可用面
  追平后端能力面：(1) 常见 provider（OpenAI/DeepSeek）仅凭 API key 即可开箱跑通
  Conversation，链路 `LLMBackend → Character → CapabilitySet → Actor` 由系统
  持有默认预设并自动 provision，且内置这两家的模型 pricing list；(2)
  Conversation 列表带 summary、支持单删 + 批量删；(3) 其余 CRUD 页面风格统一、
  动作闭环，视觉以研究员 approve 的 demo 为基线。
- **Stopping point**: 研究员打开 Admin → 填一个 OpenAI 或 DeepSeek 的 API key
  → 即可创建并跑通 Conversation（不手工建 LLMBackend/Character/CapabilitySet/
  Actor，不查 pricing 页）；→ Conversation 列表每条带 summary、可勾选批量删；
  → 整体 CRUD 视觉统一，且研究员已 approve 该 demo。
- **Links**: ISSUE-0005, ISSUE-0006, ISSUE-0007（核心 P1）。
