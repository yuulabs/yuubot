# Lesson — Milestone 内聚：不混无关切面

**Date**: 2026-06-24
**Related**: M-01 reschedule → M-01..M-05（commit `b5376d7`）
**Type**: 规划纪律固化，防死灰复燃

## What

原 M-01 "Agent Runs Right + IM Reaches Actor" 把两个正交切面捆成一个
stopping point：(1) Agent Infra（本地、不需服务器：venv 隔离 + Python 执行）
与 (2) IM 集成（需 always-on Actor Path + 稳定服务器）。两者唯一的共同点是
"都属于 Charter Phase Goal"，在技术依赖上无关。reschedule 将其拆为：
M-01（Agent Infra，现 completed）、M-02（Admin UX）、M-03（Pull 集成）、
M-04（部署）、M-05（IM，承接原 IM 半边）。

## Why

服务器预算约束一卡，IM 整条线动不了，连带 M-01 整个 milestone 即便 Agent
Infra 部分（ISSUE-0001）已 implemented 也无法标 completed —— 因为 stopping
point 把"至少一个 IM 发消息"也焊死在内。拆开后 M-01 立即 completed，
其余各有各的路径，IM 延后到 M-05 不再阻塞任何前端/集成/部署进展。

## Why A Lesson (Not Just A Trade-off)

"把能顺手一起做的事捆进一个 milestone" 是持续存在的规划冲动 —— 看起来高效，
实则把无关的依赖图焊死，使一个外部约束卡住整组工作。本 lesson 固化为硬约束：
**一个 milestone = 一个技术停止点，不混无关切面。宁可 milestone 多一点。**

内聚判据：若 milestone 的两半 stopping point 能各自独立"done"，且各自有独立
的依赖前置，它们就是两个 milestone，不是一个。字面上的"相关性"（同属一个
charter 条目）不构成捆绑理由 —— 捆绑理由必须是技术依赖上的强耦合。

## YuuPM's Judgment

**校正**(good) —— 这次 reschedule 不是规划漂移，是把一个过度捆扎的 milestone
拆成其本应有的内聚单元。外部约束（服务器预算）本不应能卡住一个与它无关的
milestone；之所以卡住，正是因为无关切面被错误捆绑。根治方法是内聚判据，
而非"下次注意服务器依赖"。

## 附带：sprint.md 冻结范围解冻

本次 reschedule 的直接机械后果：ISSUE-0002/0003/0004 从 M-01 re-link 到
M-05（draft）。三者原在 `sprint.md` 的 `## Frozen Scope` 内未启动，现移出本轮
sprint。这是冻结范围解冻，但根因是上方 milestone 规划问题，不单列 lesson。
`sprint.md` 已随之清理（非静默 — 见本 lesson 决策）。
