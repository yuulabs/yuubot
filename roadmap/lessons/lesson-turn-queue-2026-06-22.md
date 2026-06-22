# Lesson — Frontend Queue Will Not Return

**Date**: 2026-06-22
**Related**: turn-queue refactor TQ1 → TQ2 → TQ3(see `warroom/landing-plan/TODO.md`)
**Type**: 决策固化,防死灰复燃

## What

turn-queue 重构走了 3 阶段:

| Phase | What | Status |
|-------|------|--------|
| TQ1 | per-conversation pending queue + drain | superseded by TQ3 |
| TQ2 | 前端 always-on input + queue-state UI(队列带、Flush 按钮) | superseded by TQ3 |
| TQ3 | 删除 pending queue,保留 cancel + always-on input | **最终形态** |

最终形态(commit `bb6c771`):无 queue,`cancel_turn` 设事件 + `task.cancel()`
+ await;loop 是 `agent.turn_completed` 的唯一 emitter;Stop 按钮状态机:
idle=`[Send]`,generating=`[Stop]`,stopping=`[Stop disabled w/ spinner]`。
Input 在任何状态下都不 disable。`cancel_turn` 返回 `{cancelled: bool}`。

## Why

TQ1/TQ2 的 queue 机制引入了不必要的复杂度(pending queue + drain + 前端
queue-state UI),而 TQ3 证明了:**直接 cancel + always-on input 就够了**。
用户输入框始终可用,Stop 按钮处理进行中状态,无需中间 queue。

## Why A Lesson (Not Just A Trade-off)

前端 queue 类需求有反复出现的倾向——"输入发出去时 agent 还在跑怎么办"是
自然的 UX 冲动,容易有人(或 AI 生成代码时)重新引入 queue 机制。本 lesson
固化为硬约束:**不要重新引入前端 queue**。用户输入框始终可用(caching 输入
即可,无需 queue 数据结构),Stop 按钮处理取消,这是经过验证的 UX 模型。

## Impact

- 任何"输入发出去时 agent 还在跑"的 UX 复杂度的解法是 **cancel + always-on
  input**,不是 queue。
- `activeTurnKeyRef.current` 在 2nd send-during-generation 时的 overwrite 行为
  (warroom TODO I)在 TQ3 后 UI 层不可达(Stop 替换 Send),非 bug。如未来
  重引入 always-on send-during-generation,须先修 turn-key lifecycle,否则
  会撞回 TQ2 时代的问题。
- 前端 queue-state UI(队列带、Flush 按钮)已删除,**不要加回来**。

## YuuPM's Judgment

这是一个**校正**(good)— 计划从复杂走向简单,即 TQ1/TQ2 → TQ3 的演进证明了
queue 是过度工程。不是漂移。固化此决策防止未来重蹈覆辙。
