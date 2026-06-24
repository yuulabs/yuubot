---
id: ISSUE-0009
slug: monitor-llm-timing-bug
status: approved
milestone: none
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0009: Monitor LLM Timing Bug (Observability)

对齐 Charter Phase Goal 第 1 项（Agent Infra 良好的可观测性）。
**P1 是条件的**：当我们推进到专门的可观测性 milestone 时必修；此前挂 backlog。

来源：`/home/tomorrowdawn/project/yuulabs/yuubot/warroom/TODO.md`「monitor:
Tool Execution 100% (LLM execution time reported as 0)」。

非阻塞：不影响 budget enforcement、cost event 发射、cost dashboard。但
monitor 的时长分布观感误导（Tool Execution 虚占 ~100%）。

## Problem

monitor 的 time-distribution view 把 LLM 步骤的 duration 当 0 报，导致 Tool
Execution 比例虚高到 ~100%。

recon 指向发射侧：

- monitor.tsx 消费 `packages/yuutrace/.../cli/db.py` 的 analytics 查询层。
- 怀疑区：LLM timing emission — 喂给 monitor 的 duration attribute 对 LLM
  step 是 0 / missing，对 tool step 正常。
- 候选源：`apps/yuubot/.../core/assembly/_runtime.py`（agent loop）或
  `packages/yuutrace` 的 `llm.finished` span attributes。
- 引入点：cost-guard merge（见 `main` 上 merge `feature/cost-guard-web` 的
  commit）。

## User-System Scenario

```
研究员打开 Admin Monitor 页
  → time-distribution view 显示 LLM 步骤的真实时长占比（>0）
  → Tool Execution 比例归位（不再是虚高的 ~100%）
  → 修复后，研究员能用该视图判断 "这一轮里时间花在 LLM 还是 tool 上"
```

## Scope (lazy: 只列用户观测点)

- 修 LLM step 的 duration 发射（源在 `_runtime.py` agent loop 或 yuutrace span
  attrs）或在查询投影 sink 侧补齐 — 具体落点是 YuuDev 实现，本 Issue 不约束。
- 修复后 monitor.tsx 的 distribution view 显示真实 LLM/tool 时间占比。

## Out of Scope

- monitor 视图本身的重新设计 / 新增 metrics — 非本 Issue，只修 bug。
- cost dashboard / budget enforcement — 不受此 bug 影响，不在此范围。
