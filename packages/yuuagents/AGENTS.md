# packages/yuuagents

Agent runtime primitives shared across YuuLabs apps. Pure execution core with
no transport/HTTP of its own — `yuubot` (the host app) drives it. Public API is
re-exported from `src/yuuagents/__init__.py` (see `__all__` there).

This package is a workspace member of the monorepo at the repo root. Sibling
packages (`yuullm`, `yuutools`, `yuutrace`) resolve as workspace sources, not
vendored copies.

## Source Map (`src/yuuagents/`)

| Path | Responsibility |
|---|---|
| `agent/agent.py` | `Agent` — the pure LLM execution state machine: `append` / `step` / `done` / `close`. No I/O of its own beyond the injected LLM session. |
| `agent/actor.py` | Actor lifecycle: `create_agent`, `run_agent_loop`, `close_actor_resources`, emit hooks (`emit_agent_started`, `emit_budget_exceeded`, `emit_actor_message_received`, `emit_actor_message_unhandled`), `ExampleActor`. |
| `agent/definition.py` | `AgentDefinition`, `PromptDefinition`, `LlmConfig`, `BudgetConfig`. |
| `agent/llm_backend.py` | `AgentLLMBackend` — adapts yuullm session to the agent's `step` contract. |
| `core/stage.py` | `Stage` — owns agent + mailbox + event bus; the unit of concurrency a host app runs. |
| `core/runtime.py` | `Runtime` — multi-stage orchestrator (start/stop/step all stages). |
| `core/mailbox.py` | `MailBox`, `MailMessage`, `ScheduleTriggerMessage`, `BackgroundCompletedMessage`. The async queue a stage drains. |
| `core/eventbus.py` | `EventBus`, `RuntimeEvent`, `EventName` — typed runtime events consumed by observers. |
| `core/budget.py` | `Budget` — token/cost/turn ceiling per agent run. |
| `core/task.py` | `Task`, `TaskStatus`, `Owner`, `OwnerType` — durable task record for delegate/background work. |
| `core/registry.py` | Plugin/extension point registry. |
| `llm/session.py` | `ProviderPoolSessionFactory` — builds the LLM session `Agent.step` uses. |
| `obs/observability.py` | `TraceContextProvider` protocol, `DefaultTraceContextProvider`, `YuuTraceObserver` — bridges `eventbus` events to OTEL spans. Hosts (yuubot) supply a richer provider. |
| `obs/entitylog.py` | `EntityLog`, `PeriodicReporter`, content/process/command blocks — human-readable runtime log of agent activity. |
| `tool/primitives.py` | `Tool`, `ToolDefinition`, `ToolResult`, `ToolRegistry`, `ToolContext`, `ToolCallParams`, `ToolCallTask`, `register_tool_type` / `resolve_tool_type`. The tool type system used by `Agent.step`. |
| `tool/files.py` | `ReadTool`, `WriteTool`, `EditTool`, `WorkspaceFiles`, `FileToolConfig`. |
| `tool/bash.py` | `BashTool`, `BashRunner`, `BashParams`, `BashToolConfig`. |
| `python/runtime.py` | `PythonRuntime`, `ResolvedPythonRuntime`, `PythonImport`, `PythonKernelConfig` — ipykernel-backed code execution backend. |
| `python/session.py` | `PythonSession`, `PythonExecResult`, `PythonResultItem`, `MimeBundle`, `PythonSessionLike` — the live kernel session; emits structured result items (text/image/etc). |
| `types/errors.py` | `TaskError`. |
| `types/values.py` | Value/enum constants. |

## Execution model (quick reference)

```text
Host (yuubot) calls Runtime / Stage
  → MailBox delivers a MailMessage
    → create_agent() builds an Agent bound to an LLM session + ToolRegistry
      → run_agent_loop():
          Agent.append(user_message)
          while not Agent.done:
            Agent.step()      # → llm_backend → yuullm session → provider stream
            if tool calls:    # ToolRegistry resolves + dispatches, returns ToolResult
              Agent.append(tool_result)
          Agent.done → final assistant message
        → EventBus emits RuntimeEvent → YuuTraceObserver writes OTEL spans
```

Key invariants to preserve when editing:

- `Agent` stays **pure**: no network, no DB, no `asyncio` tasks of its own.
  All I/O goes through the injected LLM session and tool backends.
- Hosts own the mailbox/event bus lifecycle; `Stage` is the only thing that
  drains a mailbox.
- Tools are explicit (`ToolDefinition` + `ToolRegistry`) — never dispatch via
  stringly-typed names bypassing the registry.

## Project guidance

- Do **not** make attrs or msgspec models frozen by default. Use mutable models
  unless there is a concrete local reason that outweighs the extra construction
  and extension friction.

## Commands

```bash
uv sync                 # from monorepo root (resolves all workspace members)
uv run pytest           # from this package directory
uv run pytest tests/test_<x>.py -v
uv run ruff check src/ tests/
uv run ty check src/
```
