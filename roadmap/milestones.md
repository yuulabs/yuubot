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

- **Status**: `completed`
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

## M-03 — Grounded Research + Skills

- **Status**: `WIP`
- **Builds**: 对齐 Charter Phase Goal 第 1 项（Agent Infra 扩展点稳定）与第 5 项
  （研究员获得可解释分析）。让 yuubot 成为可自用的 grounded research assistant：
  能上传资料，能上网搜索/读取实时资料，能加载全局/局部 skills 以接入特定平台或
  特定信息收集策略。
- **Stopping point**: 研究员在 Admin 配置一个 Actor → 启用全局/局部 skills →
  上传 PDF/论文/资料 → 让 Agent 使用通用 web search/read 或第三方 skill 收集信息
  → Agent 基于文件和网页资料带引用回答。
- **Links**: ISSUE-0015, ISSUE-0013, ISSUE-0012（核心 P1）。

## M-04 — Learning Actor Communication

- **Status**: `draft`
- **Builds**: 对齐 Charter Phase Goal 第 5 项（可解释性）。学习场景的关键不是先做
  知识库平台，而是让专门的 Learning Actor 会建立心智模型、解释抽象层次、维护
  学习进度，并把沟通混乱留在内部。
- **Stopping point**: 研究员让 Learning Actor 学习 CuTe DSL 等主题 → Actor 在
  workspace `AGENTS.md` 中跟踪学习进度、当前心智模型、未解决问题和下一步 →
  对话中优先解释概念层次与上下游关系，只在用户意图会改变学习路径时提问。
- **Links**: ISSUE-0018（核心 P1；Learning Actor prompt/workspace 契约待单独
  Issue 化）。

## M-05 — Personal Deployment

- **Status**: `draft`
- **Builds**: yuubot 可部署到稳定服务器常驻运行。目标是先满足研究员个人远程自用，
  不把 IM 接入作为部署前置。
- **Stopping point**: yuubot 部署并跑在某台稳定服务器上，重启后自动恢复，
  Admin 可远程访问，M-03/M-04 的研究与学习能力可在公网环境稳定使用。
- **Links**: _（相关 Issue 待 triage。）_

## M-06 — Scheduled Follow-up

- **Status**: `draft`
- **Builds**: 对齐 Charter Phase Goal 第 5 项（overnight 运行实验、结果保留、实时
  信息跟进）。让 Actor 能安排未来任务、自唤醒，并复用情报 skill 做每日/定时检查。
- **Stopping point**: 研究员配置 Actor 定时检查 infra / CuTe / GPU kernel / 论文等
  来源 → Actor 到点自唤醒、收集变化、留下带引用摘要或实验结果报告。
- **Links**: ISSUE-0016（核心 P1），ISSUE-0017（P2）。

## M-07 — IM Reaches Actor

- **Status**: `draft`
- **Builds**: 对齐 Charter Phase Goal 第 3 项（集成框架稳定，IM 部分）。IM 集成框架
  + always-on Actor Path。IM 是外部触点增强，不阻塞 researcher MVP。
- **Stopping point**: 至少一个 IM（QQ 或 Telegram）发消息 → Actor 回复。
  两个都做完是 sprint 内延伸，不单列验收门槛。
- **Links**: ISSUE-0002（核心 P1），ISSUE-0003, ISSUE-0004（plumbing P2）。
