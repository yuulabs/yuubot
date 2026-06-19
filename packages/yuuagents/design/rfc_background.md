# RFC Background: Engine-Level Tool Timeout with Host-Managed Lifecycle

## Problem

When `execute_python` (or any tool) runs a long task (minutes to tens of minutes):

1. **Blocking execution** makes the agent unresponsive — the LLM cannot respond to
   the user or process other tool calls until the tool returns.
2. **Offloading to `TASKS`** (current workaround) loses framework control — the
   engine doesn't know background work exists, so lineage timeout can kill the
   kernel session while tasks are still running.
3. There is no way to observe background task output (streaming stdout, status)
   without the LLM actively polling.

## Core Insight

The original RFC (Thread/Syscall model) had a backgrounding mechanism where the
Engine owned thread lifecycle, wrote synthetic tool results to close LLM
tool-call protocol, and notified parents when background threads completed.

RFC2 removed this in favor of thin-loop simplicity. The minimal path is to bring
back *only the backgrounding/TODO semantic* without the full Thread/Syscall model.

## Principles

1. **Engine manages birth, host manages death.**
   - Engine: timeout detection, protocol closure (synthetic tool result),
     opaque handle storage, result collection.
   - Host (MateRunner): lifecycle tracking, lineage guard, result injection,
     proactive turn driving.

2. **General mechanism, not tool-specific.**
   Every tool call is subject to a unified `tool_timeout_s` budget (default 300s).
   No tool declares itself "backgroundable" — the engine decides uniformly.

3. **Protocol correctness.**
   Every LLM tool call must be closed by a tool result with the matching
   `call_id`. The engine writes a synthetic tool result on timeout. Background
   completion is injected as a `user` message (the original call_id is already
   consumed).

4. **Opaque handles, dependency inversion.**
   Engine stores an opaque `BackgroundTaskHandle` per task. It provides no
   tool-specific inspection API. The `yuuagents.kernel.background` RPC module
   (kernel → engine → handle forwarding) enables Python-side output reading
   without the engine knowing tool internals.

5. **Output streaming via `call_id` on `PythonSession`.**
   `PythonSession.execute()` accepts an optional `call_id`. When set, the session
   appends iopub `stream` messages to a buffer keyed by `call_id`. External
   callers read incrementally via `session.read_output(call_id, offset)`.

6. **Context grouping via `context_id`.**
   Background tasks are grouped by `context_id` (provided by host at agent
   creation time). This survives agent rollover — a new agent with the same
   `context_id` inherits tracking of the lineage's background tasks.

---

## Architecture

### Engine Responsibilities (Birth)

```
Tool call starts
  → engine wraps tool.run() in asyncio.create_task + shield
  → await asyncio.wait_for(task, timeout=tool_timeout_s)

  ├─ [completes within timeout]
  │   → normal tool result → yield ToolStep

  └─ [timeout]
      → engine stores _BackgroundTaskState{task_id, context_id, handle, status}
      → engine writes synthetic tool result: tool(call_id, "[background] ... task_id=xxx")
      → engine yields BackgroundStep(call_id, tool_name, task_id)
      → engine continues loop — LLM can proceed
      → background asyncio.Task runs to completion (shielded)
      → upon completion, engine stores result in _completed_results[task_id]
```

### Host Responsibilities (Death)

```
Host receives BackgroundStep
  → records task_id → context_id mapping (not agent_id — survives rollover)

Host checks before closing lineage:
  → engine.get_background_status(context_id) → any running? if yes, skip close

Host drives agent turns:
  → before driving, engine.collect_background_results(context_id)
  → for each completed result:
      agent.append_message(user("[background] Task 'xxx' completed:\n{result}"))
  → if any results collected, trigger new turn (proactive notification)
```

### Python-Side Observability (RPC)

```
Kernel side: yuuagents.kernel.background
  background.list_tasks()
  background.output(task_id, offset=0)

Kernel → RPC → engine._get_task_handle(task_id) → handle.read_output(offset)
  → engine only forwards; doesn't understand output content
```

---

## Data Model

### BackgroundStep (steps.py)

```python
@attrs.define(frozen=True)
class BackgroundStep(AgentStep):
    agent_id: str
    call_id: str
    tool_name: str
    task_id: str
```

### BackgroundTaskHandle (Protocol)

```python
class BackgroundTaskHandle(Protocol):
    task_id: str

    async def status(self) -> Literal["running", "waiting_input", "done", "error"]:
        """Return current status."""
        ...

    async def read_output(self, offset: int = 0) -> tuple[str, int]:
        """Return (text_since_offset, new_cursor). Tool-specific."""
        ...

    async def collect_result(self) -> yuullm.ToolOutput:
        """Collect final result after the tool completes."""
        ...
```

### Engine Internals

```python
@attrs.define(slots=True)
class _BackgroundTaskState:
    task_id: str
    tool_name: str
    context_id: str
    asyncio_task: asyncio.Task
    handle: BackgroundTaskHandle
    status: Literal["running", "done", "error"]
    result: yuullm.ToolOutput | None = None
    error: str | None = None
```

---

## Implementation Plan

### yuuagents Changes

| Module | Change |
|--------|--------|
| `steps.py` | Add `BackgroundStep` |
| `python_session.py` | Add `call_id` parameter to `execute()`, `_exec_buffers` dict, `read_output()` method. Add `PythonBackgroundHandle` implementing `BackgroundTaskHandle`. |
| `tools.py` | Add `BackgroundTaskHandle` Protocol. |
| `engine.py` | Add `_background_tasks: dict[str, _BackgroundTaskState]`, `_completed_results: dict[str, yuullm.ToolOutput]`, `_context_tasks: dict[str, set[str]]`. Add `tool_timeout_s` config. Add methods: `_move_to_background()`, `get_background_status(context_id)`, `collect_background_results(context_id)`, `_get_task_handle(task_id)`. Accept `context_id` in `create_agent()`. |
| `agent.py` | In `_run_tool_call()`: wrap in `wait_for(shield(task), timeout)`. On timeout: call `engine._move_to_background()`, write synthetic tool result, yield `BackgroundStep`. Store `context_id` on Agent. |
| `kernel.py` | Add `background` module (`_BackgroundFacade` class) with `list_tasks()`, `output()`. RPC channel registered at bootstrap. |
| `__init__.py` | Export `BackgroundStep`, `BackgroundTaskHandle`. |

### yuubot Changes

| Module | Change |
|--------|--------|
| `mate/runner.py` | Pass `context_id=lineage_id` to `engine.create_agent()`. In `_lineage_expired()`: check `engine.get_background_status(lineage_id)` before closing. In `_drive_agent()`: call `engine.collect_background_results(lineage_id)`, inject results as `user` messages. |
| `mate/watcher.py` | Poll for completed background results; when found, trigger a new turn for the lineage. |

---

## Sequence: Long-Running Download

```
1. User: "下载这个文件"
2. LLM: execute_python(code="await download(url)", timeout_s=None)
3. Engine: wait_for(shield(task), timeout=300)
4. ...290s... 下载中...
5. Engine: timeout → _move_to_background()
   - Stores state{task_id="abc", handle=PythonBackgroundHandle(call_id="xyz")}
   - Writes tool(call_id, "[background] execute_python moved to background. task_id=abc")
   - Yields BackgroundStep(task_id="abc")
6. LLM sees synthetic result → replies "还在下载中"
7. ...710s... 下载完成 (total 1010s)
   - shield'd asyncio.Task completes
   - handle.collect_result() → engine._completed_results["abc"] = result
8. MateWatcher polls → engine.collect_background_results(lineage_id)
   → returns {"abc": result}
9. MateRunner: agent.append_message(user("[background] Task 'abc' completed:\n..."))
   → triggers new turn
10. LLM sees injected user message → replies "下载完成！"
```

## Sequence: Observability During Long-Running Task

```
1. LLM: inspect_background_tasks()  (or background.list_tasks() in Python)
   → engine.get_background_status(context_id)
   → [{task_id: "abc", tool_name: "execute_python", status: "running"}]

2. LLM (in Python code):
   from yuuagents.kernel import background
   output, cursor = await background.output("abc", offset=0)
   # output: "Downloading... 45% complete\nError: connection reset\nRetrying..."

3. LLM filters in Python:
   import re
   errors = re.findall(r"Error: (.+)", output)
   # Only relevant parts passed to LLM context
```

---

## Non-Goals

- **No Thread/Syscall model.** Only backgrounding/timeout semantics, not full
  thread lifecycle management.
- **No framework-level stdin injection.** Python code that creates subprocesses
  already holds `proc.stdin`. The LLM writes stdin in Python code, not through
  engine.
- **No per-tool timeout configuration.** Universal `tool_timeout_s` on engine.
  Per-tool timeout can be added later if needed.
- **No engine-level output inspection API.** Output reading goes through
  `yuuagents.kernel.background` RPC, not engine public API.
