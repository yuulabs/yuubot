# yuubot Sprint

> 当前 sprint 的具体、可观测输出。每个 milestone 必须推进 `constitution.md` 的 Current Goal。
> REQ 文件是场景契约的唯一真相源 — 本文只引用 REQ-ID,不复述场景。

## Current Milestone

**Agent Runs Right + IM Reaches Actor**

对齐:`roadmap/constitution.md` M1 (Agent Infra 功能正常) + M3 (集成框架稳定起点)。

研究员可以:启动 yuubot → 在 Admin Conversation 让 Agent 在 workspace
里跑 Python(venv 隔离、预装数据科学包、自检可用包、可装新包、可重启
kernel)→ 通过 QQ 或 Telegram 给 Actor 发消息 → Actor 回复。

## Core Requirements

核心 REQs(12h 核心预算,对齐 Hofstadter × Pareto 的有效产出估算):

| REQ | Title | Est.h | Type | Aligns |
|-----|-------|-------|------|--------|
| REQ-0001 | Agent Python Execution Environment | 7 | 核心 | M1 |
| REQ-0002 | IM Integration Framework | 5 | 核心 | M3 |

非核心 plumbing(sprint 真实日历,不占 12h 预算):

| REQ | Title | Est.h | Type | Derived from |
|-----|-------|-------|------|---------------|
| REQ-0003 | QQ Instance | 1.5 | 非核心 plumbing | REQ-0002 |
| REQ-0004 | Telegram Instance | 1.5 | 非核心 plumbing | REQ-0002 |

milestone 停止观测点:至少一个 IM 发消息 → Actor 回复。两个都做完是
sprint 内延伸,不单列验收门槛。

## Blockers

_无。_ (warroom `monitor` LLM 计时显示 bug 非阻塞,挂 backlog。)

## Trade-offs

本 sprint 明确不做:

- **不做前端 CRUD 美化**(warroom landing-plan TODO A/B/D),后端稳定优先。
- **不做前端 model-context inspector**(Phase 5.5),维持 deferred。
- **不做 partial tool_call 流式增强**(warroom landing-plan TODO H)。
- **不修复 monitor LLM 计时 bug**(warroom TODO 主条),非阻塞,挂 backlog。
- **不做 runtime tuning 字段持久化**(warroom landing-plan TODO C),依赖前端
  Panel(B)才有意义。
- **不重写 turn-queue 生命周期**——TQ1/TQ2 已被 TQ3 supersede(最终形态:无
  queue,保留 cancel + always-on input)。新 lesson 固化此决策,防死灰复燃。

## Velocity

_TBD — too few REQs to measure._ 首次 sprint,无历史 actual/estimated 数据
计算系数。估算直接给出,标不确定性。
