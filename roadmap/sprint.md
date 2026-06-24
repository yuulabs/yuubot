# yuubot Sprint

> 当前 sprint 的具体、可观测输出。每个 milestone 必须推进 `charter.md` 的 Phase Goal。
> Issue 文件是场景契约的唯一真相源 — 本文只引用 ISSUE-ID，不复述场景。

## Current Milestone

**M-01 "Agent Runs Right + IM Reaches Actor"**（见 `milestones.md`，WIP）。

对齐：`charter.md` Phase Goal 第 1 项（Agent Infra 功能正常）+ 第 3 项
（集成框架稳定起点）。

研究员可以：启动 yuubot → 在 Admin Conversation 让 Agent 在 workspace
里跑 Python（venv 隔离、预装数据科学包、可自检可用包、可装新包、可重启
kernel）→ 通过 QQ 或 Telegram 给 Actor 发消息 → Actor 回复。

## Blockers

_无。_ （warroom `monitor` LLM 计时显示 bug 非阻塞，挂 backlog。）

## Frozen Scope

> Freeze commit: `ade7000` → 本轮重新映射为 ISSUE-ID（scenario 契约不变，
> 仅 schema 迁移）。scope 内容未改变。

核心 Issue（核心预算参照线 ~12h，用于判断核心堆是否过载，不是硬约束，
更不是估算器）：

| Issue | Title | Est.h | Priority | Type | Aligns |
|-------|-------|-------|----------|------|--------|
| ISSUE-0001 | Agent Python Execution Environment | 7 | P1 | 核心 | M-01 (Agent Infra) |
| ISSUE-0002 | IM Integration Framework | 5 | P1 | 核心 | M-01 (IM Framework) |

非核心 plumbing（sprint 真实日历，不占 12h 预算）：

| Issue | Title | Est.h | Priority | Type | Derived from |
|-------|-------|-------|----------|------|---------------|
| ISSUE-0003 | QQ Instance | unknown | P2 | 非核心 plumbing | ISSUE-0002 |
| ISSUE-0004 | Telegram Instance | unknown | P2 | 非核心 plumbing | ISSUE-0002 |

milestone stopping point：至少一个 IM 发消息 → Actor 回复。两个都做完是
sprint 内延伸，不单列验收门槛。

## Trade-offs

本 sprint 明确不做：

- **不做前端 CRUD 美化**（warroom landing-plan TODO A/B/D），后端稳定优先。
- **不做前端 model-context inspector**（Phase 5.5），维持 deferred。
- **不做 partial tool_call 流式增强**（warroom landing-plan TODO H）。
- **不修复 monitor LLM 计时 bug**（warroom TODO 主条），非阻塞，挂 backlog。
- **不做 runtime tuning 字段持久化**（warroom landing-plan TODO C），依赖前端
  Panel（B）才有意义。
- **不重写 turn-queue 生命周期**——TQ1/TQ2 已被 TQ3 supersede（最终形态：无
  queue，保留 cancel + always-on input）。新 lesson 固化此决策，防死灰复燃。

## Velocity

_TBD — too few Issues to measure._ 首次 sprint。ISSUE-0001..0004 完成后从
`git log` 读取 created→implemented 实际耗时，展示趋势（不计算 coefficient，
不以此倒推未来估算——首试水 8→4→5h 的预算倒推已证此路不通。用户读趋势自行
判断）。
