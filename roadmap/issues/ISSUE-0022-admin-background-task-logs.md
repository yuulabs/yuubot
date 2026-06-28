---
id: ISSUE-0022
slug: admin-background-task-logs
status: draft
milestone: none
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0022: Admin Background Task Logs

## Problem

Once background tools exist, users need a reliable place to see what is still
running, what finished, what failed, and why. Conversation notices are useful
for context, but they are not enough for debugging long-running tasks or reading
large stdout/stderr streams. The desired experience is close to Docker logs:
open a task, follow output, inspect status, and cancel if needed.

## User-System Scenario

```text
Researcher opens Admin task view
  → Sees running and recent background tasks
  → Opens one task
  → Watches stdout/stderr in a Docker logs-style panel
  → Cancels a running task when needed
  → Follows links back to the originating conversation and actor
```

## Scope

- Add task read APIs for Admin:
  list tasks, get task detail, fetch task logs by stream and offset, cancel task.
- Add task event streaming through SSE for status/log follow behavior.
- Add an Admin Tasks page or drawer with task list and detail view.
- Show task metadata: task id, tool, status, actor, conversation, start time,
  duration, timeout, and terminal error/result summary.
- Render stdout/stderr in a Docker logs-style viewer with follow behavior.
- Link task records back to their originating conversation where available.

## Out of Scope

- WebSocket migration; SSE plus HTTP is sufficient for server-push status and
  user-initiated commands.
- Interactive stdin or terminal attach.
- Replaying or re-running tasks from the UI.
