---
id: ISSUE-0021
slug: background-tool-execution-and-completion-notifications
status: draft
milestone: none
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0021: Background Tool Execution and Completion Notifications

## Problem

Some tasks need to outlive the foreground 240s tool limit. Reusing
`execute_python` with `background=True` would conflict with the live shared
Python session and make foreground state, kernel locks, cancellation, and
background execution interfere with each other. Bash can run long commands, but
it lacks the actor facade context that makes `yb`, `tim`, and `yext.*` useful.

The platform needs explicit background tools with clear semantics and automatic
completion notifications.

## User-System Scenario

```text
Agent identifies long-running shell or Python work
  → Agent submits it through a background tool
  → System immediately returns a task_id
  → Background work runs under Task infra with logs, status, timeout, and cancel
  → On completion/failure/timeout/cancel, System injects a conversation notice
  → Agent and user can continue from the completed task result
```

## Scope

- Add a background bash surface for workspace-scoped shell work.
- Add `background_python` as a separate tool, not a flag on `execute_python`.
- `background_python` uses a fresh one-shot `PythonSession` per task and closes
  it after completion, failure, timeout, or cancel.
- Reuse the existing `execute_python` runtime derivation for background Python:
  workspace venv, cwd, sys_path, startup code, `yb`, `tim`, `yext.*`, and
  `SESSION_STATE`.
- Persist task status and stdout/stderr through Task infra.
- Inject conversation notifications when background tasks start and when they
  reach a terminal state.
- Provide cancel support for running background tasks.

## Out of Scope

- Sharing variables between foreground `execute_python` and background Python.
- Requiring agents to build their own callback protocol with screen/tmux.
- Full Admin task browser UI; covered by ISSUE-0022.
- Remote-host callback API. A future callback surface may complement Task
  infra, but it is not the default background execution model here.
