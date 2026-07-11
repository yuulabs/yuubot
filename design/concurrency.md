# Data Concurrency Principles

> 本文是 [`system-design.md`](system-design.md) 的并发专题补充；系统整体设计与外部 facade
> 以该文档为准。

Yuubot has several async boundaries: HTTP/WebSocket requests, actor mailboxes,
conversation loops, background tasks, event listeners, and SQLite persistence.
Correctness depends on each mutable datum having one owner.

## Ownership

- `Conversation` owns one conversation's run gate, interrupt flag, durable
  history append order, status transitions, and conversation-bound task
  delivery queue.
- `TaskScheduler` owns task process lifecycle and terminal task transitions.
- Task delivery state is changed only by the task delivery coordinator path in
  `runtime.tasks`.
- `EventBus` is an observation and fanout surface. It must not be the only path
  that performs required business work.
- SQLite is the durable fact store. Multi-row business changes must use an
  explicit transaction when partial visibility would be invalid.

## Rules

- Do not mutate owner internals from tests or other modules to create a state.
  Drive the public behavior that creates the state.
- Do not split one state transition across an event listener and a caller-owned
  cleanup hook. The owner API should decide queue, deliver, or skip in one
  place.
- Keep direct user conversation sends busy rather than queued. Queue only
  system follow-ups that are explicitly designed to resume later, such as task
  delivery.
- Keep cancellation and interrupt idempotent. A late task completion after
  interrupt must skip deterministically, not depend on listener timing.
- Log development-mode transition decisions near the owner boundary so timing
  failures can be diagnosed from `data_dir/logs/yuubot.log`.

## Target Scenario

```text
task reaches terminal
  -> TaskScheduler records terminal state
    -> task delivery coordinator asks the Conversation owner
      -> Conversation queues, delivers, or skips based on its gate state
        -> actor mailbox receives exactly one follow-up or a skip is recorded
```
