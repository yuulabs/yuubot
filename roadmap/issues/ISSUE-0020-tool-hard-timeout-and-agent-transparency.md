---
id: ISSUE-0020
slug: tool-hard-timeout-and-agent-transparency
status: draft
milestone: none
priority: P2
estimated_work_hours: unknown
---

# ISSUE-0020: Tool Hard Timeout and Agent Transparency

## Problem

Tool timeout semantics are currently split across Runtime waiting, per-tool
timeouts, and agent-turn cancellation. This makes the platform harder to reason
about and makes the behavior opaque to the agent. A foreground tool can appear
to hang from the user's perspective, while the agent does not have a clear rule
for when to switch to a background path.

## User-System Scenario

```text
Agent calls a foreground tool
  → System enforces a hard 240s execution limit
  → If the tool exceeds the limit, System interrupts/kills it
  → Agent receives a clear timeout tool result
  → Agent knows from the prompt that foreground tools are limited
  → Agent chooses background tools for long-running work
```

## Scope

- Enforce a default 240s hard timeout for foreground tool execution.
- Make timeout cancellation real, not just a waiting-side status mark.
- Ensure built-in tools implement coherent cancellation semantics:
  `bash` kills the process group; `execute_python` interrupts/closes the live
  kernel; file tools return a normal cancellation result where applicable.
- Return a consistent timeout result to the agent.
- Update system prompt and relevant tool descriptions so the agent sees the
  240s foreground limit and the background-task alternative.

## Out of Scope

- Long-running background execution surfaces; covered by ISSUE-0021.
- Admin task log UI; covered by ISSUE-0022.
- Interactive terminal or stdin support.
