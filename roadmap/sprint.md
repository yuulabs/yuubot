# yuubot Sprint

> 当前 sprint 的具体、可观测输出。每个 milestone 必须推进 `charter.md` 的 Phase Goal。
> Issue 文件是场景契约的唯一真相源 — 本文只引用 ISSUE-ID，不复述场景。

## Current Milestone

**M-02 "Admin UX Equals Backend Surface"**（见 `milestones.md`，WIP）。

对齐：`charter.md` Phase Goal 第 2 项（前端完成）。让 Admin UI 的可用面追平
后端能力面。

> 上一轮 sprint（M-01）已随 milestone reschedule 结清：ISSUE-0001 implemented，
> ISSUE-0002/0003/0004 re-link 到 M-05 并移出（见
> `lessons/lesson-milestone-cohesion-2026-06-24.md`）。

本轮 sprint **短于两周**（用户明确），预算按实际长度等比下调，不作两周标定。

## Blockers

_无。_

## Frozen Scope

落地顺序依依赖关系：0011（删 Character 顶层页）→ 0010（入口收敛到 Actor）→
0007（CRUD 视觉基线）。0011 必须在 0007 前落地，否则会给即将被删的 characters
页做基线返工。0010/0011 是结构收敛，0007 是视觉基线，结构先于视觉。

| Order | Issue | Pri | Est.h | 角色 |
|-------|-------|-----|-------|------|
| 1 | ISSUE-0011 | P2 | 1 | Character 折叠进 Actor |
| 2 | ISSUE-0010 | P2 | 4 | Conversation 入口收敛到 Actor |
| 3 | ISSUE-0007 | P1 | 8 | CRUD 视觉基线（demo 迭代至 approve → 落地） |
| **合计** | | | **13h** | core 8h（0007）+ body 5h（0011+0010） |

Core（P0+P1）= 8h，刻意低于 12h 常规门槛 —— 本轮是结构先行 sprint，把 M-02 的
P1 feature（0005 开箱即用 / 0006 Conversation CRUD）刻意推到下轮，等 0010/0011
结构收敛后 0005 的默认 Actor provision 才不在 Character 顶层页叠返工。

## Trade-offs

- **不做 ISSUE-0005**（P1, 4h，M-02 stopping point #1 开箱即用 Conversation）
  — 刻意推迟。0010/0011 落地后 0005 的 default Actor provision 才顺：character_prompt
  内联在 Actor 表单、Conversation 入口在 Actor 处，免得 provision 出来的默认
  Actor 指向一个要被收掉的 Character 顶层页。下轮 sprint 优先选。
- **不做 ISSUE-0006**（P1, 2h，M-02 stopping point #2 Conversation CRUD 删/summary）
  — 推迟。其批量删 UI 想骑 0007 本轮产出的 DataTable 基线，按依赖下轮吃更顺。
- **本轮 sprint 不让 M-02 done。** 三项 stopping point 里只有 #3（视觉基线）本轮
  推进；#1（0005）#2（0006）均推下轮。Sprint 跑完研究员仍无法"填个 API key 就
  跑通 Conversation"—— 那是下轮 0005 的事。

## Velocity

上一轮（M-01 sprint）唯一完成的 Issue：

| Issue | Est.h | Act.h | Ratio | Note |
|-------|-------|-------|-------|------|
| ISSUE-0001 | 7 | 8 | 1.14 | 首次 sprint 样本，N=1，不据此修正未来估算 |

ISSUE-0002/0003/0004 未启动即移出，无 actual。数据稀疏，不计算 coefficient。
