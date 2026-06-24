# yuubot Milestones

> 技术停止点的 chron 列表。top → bottom 读 `completed → WIP → draft`。
> 同时至多一个 `WIP`。`completed` 的判据：其 P1 Issue 全部 `implemented`。

## M-01 — Agent Runs Right

- **Status**: `completed`
- **Builds**: 对齐 Charter Phase Goal 第 1 项（Agent Infra 功能正常）。研究员可以：
  启动 yuubot → 在 Admin Conversation 让 Agent 在 workspace 里跑 Python（venv
  隔离、预装数据科学包、自检可用包、可装新包、可重启 kernel）。
- **Stopping point**: Agent 在 workspace 里跑通 Python 执行环境（见上）。
- **Links**: ISSUE-0001（核心 P1，已 implemented）。

## M-02 — Admin UX Equals Backend Surface

- **Status**: `WIP`
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

## M-03 — Integration Framework (Pull) + Query Integrations

- **Status**: `draft`
- **Builds**: 对齐 Charter Phase Goal 第 3 项（集成框架稳定起点）。优化集成框架，
  接入 pull 型查询类集成：Lark Office、Linear。pull 型集成不需要 always-on 实体，
  本地/现有服务即可跑通，绕开服务器前置约束。
- **Stopping point**: 研究员在 Admin 配置一个 Lark Office 或 Linear 的接入 →
  yuubot 能 pull 其数据供 Agent 查询（不依赖常驻服务器）。
- **Links**: _（相关 Issue 待 triage，按规则不预填。）_

## M-04 — Deployment

- **Status**: `draft`
- **Builds**: yuubot 可部署到稳定服务器常驻运行。是 M-05（IM）的前置 enabler ——
  IM 需 always-on 实体落在常驻进程上，无独立服务器承载则 IM 无处可跑。
- **Stopping point**: yuubot 部署并跑在某台稳定服务器上，重启后自动恢复，
  Admin 可远程访问。
- **Links**: _（相关 Issue 待 triage。）_

## M-05 — IM Reaches Actor

- **Status**: `draft`
- **Builds**: 对齐 Charter Phase Goal 第 3 项（集成框架稳定，IM 部分）。IM 集成框架
  + always-on Actor Path。前置依赖 M-04 已提供常驻服务器。
- **Stopping point**: 至少一个 IM（QQ 或 Telegram）发消息 → Actor 回复。
  两个都做完是 sprint 内延伸，不单列验收门槛。
- **Links**: ISSUE-0002（核心 P1），ISSUE-0003, ISSUE-0004（plumbing P2）。
