# yuuagents

A thin agent runtime built around a `Stage` resource container, explicit `Agent` LLM/tool semantics, and a `ToolBackend`/`ToolExecutor` model for tool extensibility.

## Architecture overview

```
Stage  ─ holds all shared resources (no behavior)
  ├── mailbox:   MailBox          incoming messages for the actor
  ├── eventbus:  EventBus         publish-subscribe observability
  ├── runtime:   Runtime          routes tool calls, tracks background tasks
  ├── tool_backends: Registry[ToolBackend]  factory for ToolExecutor + tool specs
  └── llm_session_factories: Registry[LlmSessionFactory]  provider-keyed yuullm sessions

ExampleActor  ─ sample mailbox/lifecycle implementation, not a base API
  └── reusable helpers:
      create_agent(definition) → Agent
      run_agent_loop(agent)       default: call_llm / call_tools loop
      emit_* helpers              standard runtime events

Agent  ─ owns one conversation lifecycle
  ├── history: yuullm.History  property over the stateful yuullm session
  ├── budget:  Budget
  ├── call_llm()   stream one LLM turn; yuullm commits the assistant message
  ├── call_tools() dispatch all pending tool calls in parallel
  ├── close()      end lifecycle and release runtime-owned resources
  └── done()       True when no pending tool calls or messages

Runtime  ─ routes ToolCall → ToolExecutor, wraps result in Task
  └── submit(agent_id, tool_call, budget) → Task
      submit_bg(task_id, metadata, bg_task)

ToolBackend / ToolExecutor  ─ plug-in tool implementations
  ToolBackend.create_executor(tool_config)  → fresh agent-owned ToolExecutor
  ToolBackend.create_tool_specs(spec_config) → list[ToolSpec]
  ToolExecutor.run(tool_name, payload, context) → ContentLike | BackgroundTask
```

## Quickstart

```python
import yuullm
import yuuagents as ya
from yuuagents.tool_backends import IpykernelToolBackend

pool = yuullm.ProviderPool({
    "anthropic": yuullm.ProviderSpec(
        name="anthropic",
        api_type="anthropic-messages",
        api_key_env="ANTHROPIC_API_KEY",
    ),
})

stage = ya.Stage(
    mailbox=ya.MailBox(),
    eventbus=ya.EventBus(),
    runtime=ya.Runtime(eventbus=ya.EventBus()),
    tool_backends=ya.Registry({
        "ipykernel": IpykernelToolBackend(),
    }),
    llm_session_factories=ya.Registry({
        "anthropic": ya.ProviderPoolSessionFactory(
            pool=pool,
            selector="claude-sonnet-4-6",
        ),
    }),
)

definition = ya.AgentDefinition(
    name="analyst",
    llm=ya.LlmConfig(provider="anthropic", model="claude-sonnet-4-6"),
    budget=ya.BudgetConfig(max_steps=20),
    tools={"ipykernel": {}},
    prompt=ya.PromptDefinition(system="Use Python for non-trivial work."),
)

agent = ya.create_agent(stage, definition)
agent.append_message(yuullm.user("Analyze this data."))

while not agent.done():
    await agent.call_llm()
    await agent.call_tools()

await agent.close()
```

### Using the ExampleActor sample

`ExampleActor` is a small reference implementation. Use it for examples and
tests, or write your own actor and reuse the module-level helpers:

```python
actor = ya.ExampleActor(stage)
agent = actor.create_agent(definition)
agent.append_message(yuullm.user("Hello."))
await actor.run_agent_loop(agent)
await actor.expire_agent(agent)
```

## AgentDefinition

Three equivalent construction paths — same internal representation:

```python
# 1. From a TOML file
definition = ya.AgentDefinition.from_file("agent.toml")

# 2. From a dict (database row, HTTP response, etc.)
definition = ya.AgentDefinition.from_dict(row["config"])

# 3. Direct construction
definition = ya.AgentDefinition(
    name="shiori",
    llm=ya.LlmConfig(provider="anthropic", model="claude-sonnet-4-6", max_tokens=8096),
    budget=ya.BudgetConfig(max_steps=80, max_tokens=200_000),
    tools={
        "ipykernel": {
            "imports": [{"module": "my_app.tools", "alias": "tools"}],
            "expand_functions": ["tools.*", "-tools.delete_*"],
            "state": {"tenant_id": "acme"},
        },
        "bash": {"spec": {"level": "type-only"}},
        "fileop": {},
    },
    prompt=ya.PromptDefinition(system="You are Shiori, a helpful assistant."),
)
```

### TOML format (agent.toml)

```toml
name = "shiori"

[llm]
provider   = "anthropic"
model      = "claude-sonnet-4-6"
max_tokens = 8096

[budget]
max_steps  = 80
max_tokens = 200_000

[tools.ipykernel]
imports          = [{module = "my_app.tools", alias = "tools"}]
expand_functions = ["tools.*", "-tools.delete_*"]
state            = {tenant_id = "acme"}

[tools.bash]
[tools.fileop]
[tools.schedule]

[prompt]
system = "You are Shiori, a helpful assistant."

[tools.bash.spec]
level = "type-only"
```

`AgentDefinition.llm.provider` is a requirement, not a hint. `create_agent` resolves it against `Stage.llm_session_factories`; `AgentDefinition.llm.model` selects the yuullm session selector, while `max_tokens` and other stream options are passed to `YuuSession.stream()`.

`tools` keys must match backend keys registered in `Stage.tool_backends`. Each tool entry creates an executor and exposes that backend's tool specs. Actor-owned executors are supplied to `create_agent(..., actor_executors={...})` or `ExampleActor(actor_executors={...})` and exposed when the definition requests that key in `tools`.

## Built-in tool_backends

| ToolBackend key | Tools exposed | Description |
|---|---|---|
| `ipykernel` | `execute_python` | Persistent Jupyter kernel per agent |
| `bash` | `bash` | Shell command execution |
| `fileop` | `read_file`, `edit_file` | File read/edit operations |
| `background` | `check_background`, `write_background` | Monitor actor-owned background tasks |
| `schedule` | `create_cron`, `list_crons`, `delete_cron` | Cron scheduling (actor-owned) |
| `sleep` | `sleep` | Lightweight blocking sleep |

Import from `yuuagents.tool_backends`:

```python
from yuuagents.tool_backends import (
    IpykernelToolBackend,
    BashToolBackend,
    FileOpToolBackend,
    BackgroundToolBackend,
    ScheduleToolBackend,
    SleepToolBackend,
)
```

## Python Tool Config

`IpykernelToolBackend` starts one Jupyter kernel per agent. Configure it via the `ipykernel` tool config dict:

```python
from yuuagents.tool_backends import IpykernelToolBackend
import yuuagents as ya

backend = IpykernelToolBackend(
    _config=ya.PythonKernelConfig(
        cwd="/srv/workspaces/default",
        sys_path=("/srv/app/src",),
        extra_envs={"MY_APP_BASE_URL": "http://127.0.0.1:8828"},
        startup_code="import warnings; warnings.filterwarnings('ignore')",
    )
)

stage = ya.Stage(
    ...
    tool_backends=ya.Registry({"ipykernel": backend}),
)
```

tool config dict fields:

| Field | Type | Description |
|---|---|---|
| `imports` | list of `{module, alias}` | Modules pre-imported in every cell |
| `expand_functions` | list of patterns | Which functions appear in tool description |
| `state` | dict | Host-provided JSON accessible as `SESSION_STATE` |

### expand_functions patterns

Patterns match against `name`, `module.name`, or `alias.name`:

```
"tools.*"           # all functions in the tools module — show first-line docstring
"+tools.render_pdf" # show full docstring
"-tools.delete_*"   # exclude matching functions
```

If `expand_functions` is omitted, up to the first 24 public functions are listed using the tool's `spec.level` (`detail` by default).

### Session state

```python
# In execute_python cells:
state = SESSION_STATE           # dict-like, set by host
tenant_id = state["tenant_id"]
```

Dynamic state can be provided via `state_hook` callbacks on `PythonImport`:

```python
ya.PythonImport(
    "my_app.tools",
    alias="tools",
    state_hook={"request_id": lambda: current_request_id()},
)
```

## Extending execute_python: custom functions

Most agent tool configs come from Python extension modules — ordinary importable packages that the kernel pre-loads so the LLM can call their functions directly from `execute_python` cells.

### How the kernel bootstraps

When `IpykernelExecutor` starts a kernel for an agent it runs a single bootstrap cell (silent, not in history):

```python
# injected by the runtime — not written by you
SESSION_STATE = {"tenant_id": "acme", "user_id": "u_123"}   # from tool config
TASKS = {}                                                    # background task registry

# for each PythonImport(module="my_app.tools", alias="tools"):
import importlib, sys
_mod = importlib.import_module("my_app.tools")
sys.modules["tools"] = _mod   # alias registered here
```

After that, `startup_code` from `PythonKernelConfig` runs.  Everything in the kernel persists across `execute_python` calls for the agent's lifetime.

Inside any cell the agent writes, it can therefore do:

```python
import tools                       # works — alias is in sys.modules
result = await tools.search(query) # async functions supported at top level
print(SESSION_STATE["tenant_id"])  # host-injected state dict
```

### Writing an extension module

An extension module is an ordinary Python package placed on `sys_path`:

```python
# my_app/tools.py

"""Tools for querying orders and managing tickets."""

__all__ = ["search_orders", "update_ticket", "list_products"]

async def search_orders(tenant_id: str, query: str, limit: int = 20) -> list[dict]:
    """Search orders by keyword.

    Returns a list of order dicts with keys: id, status, total, created_at.
    Raises ValueError if query is empty.
    """
    ...

async def update_ticket(ticket_id: str, status: str, note: str = "") -> dict:
    """Update a support ticket status. status must be open|pending|closed."""
    ...

async def list_products(tenant_id: str, category: str = "") -> list[dict]:
    """List products, optionally filtered by category."""
    ...

def _internal_helper():   # leading underscore — never shown to the LLM
    ...
```

Key conventions:

- **`__all__`** controls which names are inspected. Without it, all public (non-`_`) functions are used.
- **Docstrings drive the tool description**. The first line becomes the summary shown in `summary` mode; the full text is shown in `detail` mode (with `+` prefix in `expand_functions`). Write them as if they are the LLM's only reference for how to call the function.
- **Async is first-class**. Top-level `await` works in kernel cells so `await tools.search_orders(...)` is natural.
- **Type annotations help** but are not required — they appear in the generated signature shown to the LLM.

### Accessing SESSION_STATE from extension functions

`SESSION_STATE` lives in the kernel's `__main__` namespace. Extension module functions run in their own module namespace and cannot see it directly. Two clean patterns:

**Pattern 1 — pass as parameter (preferred)**

```python
# The LLM passes tenant_id from SESSION_STATE explicitly:
import tools
result = await tools.search_orders(
    tenant_id=SESSION_STATE["tenant_id"],
    query="refund",
)
```

This keeps functions testable and free of kernel coupling.

**Pattern 2 — read `__main__` inside the function**

```python
# my_app/tools.py
import sys

def _get_session() -> dict:
    return sys.modules["__main__"].SESSION_STATE

async def search_orders(query: str, limit: int = 20) -> list[dict]:
    """Search orders for the current session's tenant."""
    tenant_id = _get_session()["tenant_id"]
    ...
```

Use this when you want zero-argument convenience calls from the LLM.

### Dynamic state with state_hook

`state_hook` injects runtime values into `SESSION_STATE` at the moment `create_agent` is called — before the kernel starts. Useful for request-scoped context like trace IDs or auth tokens:

```python
import uuid

ya.PythonImport(
    "my_app.tools",
    alias="tools",
    state_hook={
        "request_id": lambda: str(uuid.uuid4()),
        "db_url":     lambda: get_current_db_url(),
    },
)
```

The hook values are merged into the tool-config-level `state` dict and written into `SESSION_STATE` during bootstrap. Hooks must return JSON-serializable values.

### Fine-grained function visibility

`expand_functions` controls exactly which function signatures and docstrings appear in the `execute_python` tool description. Without it, up to the first 24 public functions are listed using the tool's `spec.level` (`detail` by default).

```python
tools={
    "ipykernel": {
        "imports": [
            {"module": "my_app.data_tools",   "alias": "data"},
            {"module": "my_app.report_tools", "alias": "report"},
        ],
        "expand_functions": [
            "data.*",              # all data functions — first-line docstring only
            "+report.render_pdf",  # render_pdf — full docstring (the + prefix)
            "+report.export_csv",
            "-data.delete_*",      # hide dangerous functions (the - prefix)
        ],
    }
}
```

Pattern matching rules:

| Pattern | Matches against |
|---|---|
| `name` | bare function name |
| `module.name` | full module path + name |
| `alias.name` | alias + name (most readable) |
| Glob `*` and `?` | standard `fnmatch` rules |

Modes:

| Prefix | Mode | Description |
|---|---|---|
| _(none)_ | summary | First line of docstring only |
| `+` | detail | Full docstring up to 4000 characters |
| `-` | exclude | Remove from tool description entirely |

Patterns are processed in order; the last match wins.

### Multiple modules / separation of concerns

Register several imports so the agent can mix and match:

```python
tools={
    "ipykernel": {
        "imports": [
            {"module": "my_app.data_tools",   "alias": "data"},
            {"module": "my_app.report_tools", "alias": "report"},
            {"module": "my_app.notify",       "alias": "notify"},
        ],
        "expand_functions": [
            "data.*",
            "report.*",
            "notify.send_email",   # expose only safe send functions
            "-notify.send_sms",    # block one specific function
        ],
    }
}
```

Inside `execute_python`:

```python
import data, report, notify

rows = await data.fetch_weekly_summary(tenant_id=SESSION_STATE["tenant_id"])
pdf  = await report.render_pdf(rows, title="Weekly Report")
await notify.send_email(to="boss@example.com", attachment=pdf)
```

### Startup code for kernel-level setup

`startup_code` in `PythonKernelConfig` runs once during bootstrap, after all imports. Use it for matplotlib backend selection, pandas display options, or any global kernel state:

```python
ya.PythonKernelConfig(
    cwd="/srv/workspace",
    sys_path=("/srv/app/src",),
    startup_code="""
import matplotlib
matplotlib.use("Agg")
import pandas as pd
pd.set_option("display.max_columns", 50)
pd.set_option("display.max_rows", 100)
""",
)
```

### TOML equivalent

All of the above configuration is expressible in the agent's TOML file with no Python code required:

```toml
[tools.ipykernel]
imports = [
    {module = "my_app.data_tools",   alias = "data"},
    {module = "my_app.report_tools", alias = "report"},
    {module = "my_app.notify",       alias = "notify"},
]
expand_functions = [
    "data.*",
    "report.*",
    "notify.send_email",
    "-notify.send_sms",
]
state = {tenant_id = "acme", env = "prod"}

[tools.ipykernel.spec]
level = "detail"
```

`level` in the optional `spec` config controls the default verbosity for functions not covered by `expand_functions`:

| `level` | Effect |
|---|---|
| `type-only` | Tool schema with no function listing |
| `summary` | First-line docstrings |
| `detail` | Full docstrings (default) |

## Extension: custom ToolBackend and ToolExecutor

Implement the `ToolBackend` and `ToolExecutor` protocols from `yuuagents.tool_backends`:

```python
from yuuagents.tool_backends import ToolBackend, ToolExecutionContext, ToolExecutor

class DatabaseExecutor:
    def __init__(self, dsn: str) -> None:
        self._db = connect(dsn)

    def __contains__(self, tool_name: str) -> bool:
        return tool_name in {"db_query", "db_upsert"}

    async def run(self, tool_name: str, payload: dict, context: ToolExecutionContext) -> str:
        context.sink.declare_free("billed externally")  # required — or call context.sink.charge()
        match tool_name:
            case "db_query":
                rows = await self._db.fetch(payload["sql"])
                return json.dumps(rows)
            case "db_upsert":
                await self._db.execute(payload["sql"])
                return "ok"
        raise KeyError(tool_name)

    async def aclose(self) -> None:
        await self._db.close()


class DatabaseToolBackend:
    def create_executor(self, tool_config: dict) -> DatabaseExecutor:
        return DatabaseExecutor(dsn=tool_config["dsn"])

    def create_tool_specs(self, spec_config: ya.ToolSpecConfig) -> list[ya.ToolSpec]:
        return _render_db_specs(level=spec_config.level)
```

Register the backend:

```python
stage = ya.Stage(
    ...
    tool_backends=ya.Registry({
        "ipykernel": IpykernelToolBackend(),
        "mydb": DatabaseToolBackend(),
    }),
)
```

Declare tool config in the definition:

```toml
[tools.mydb]
dsn = "postgresql://localhost/prod"
```

### ToolExecutor ownership

- **Agent-owned** (default): created by `create_agent`, closed by `agent.close()` / `actor.expire_agent(agent)`.
- **Actor-owned**: create and retain the executor in your host actor, then pass it via `create_agent(..., actor_executors={...})`, `ExampleActor(actor_executors={...})`, or call `runtime.add_executors(..., owned=False)`. The host actor is responsible for closing these executors.

## Observability

Subscribe any callable or object with `on_event` to the event bus:

```python
class CostLogger:
    def on_event(self, event: ya.RuntimeEvent) -> None:
        if event.name == "llm.finished":
            cost = event.data.get("cost")
            print(f"[{event.agent_name}] cost={cost}")

stage.eventbus.subscribe(CostLogger())
```

`on_event` may be a plain `def` or `async def`. Exceptions inside observers are silently swallowed so they never interrupt the agent loop.

### Event reference

| Event name | Key `data` fields |
|---|---|
| `llm.started` | `agent_id`, `agent_name`, `tool_count`, `tool_specs` |
| `llm.finished` | `agent_id`, `usage`, `cost`, `model`, `duration_s`, `message`, `text`, `tool_calls` |
| `runtime.task_created` | `agent_id`, `tool_name` |
| `runtime.task_completed` | `task_id` |
| `runtime.task_move_to_bg` | `task_id` |
| `runtime.task_error` | `task_id`, `error` |
| `runtime.task_cancelled` | `task_id` |
| `runtime.usage_reported` | `task_id`, `service`, `amount`, `unit` |
| `output.entity` | `entity_id`, `entity_type`, `parent_id`, `tool_call_id` |
| `output.chunk` | `entity_id`, `entity_type`, `parent_id`, `tool_call_id`, `chunk_index`, `blocks` |
| `output.entity_end` | `entity_id`, `entity_type`, `parent_id`, `tool_call_id`, `status` |

## Budget

`Budget` tracks cumulative usage; limits are enforced inside `run_agent_loop` (or your own loop):

```python
async def run_with_summary(agent: ya.Agent) -> None:
    reset_depth = 1
    while not agent.done():
        if agent.budget.is_exceeded():
            if reset_depth <= 0: break
            summary = await summarize(agent.history)
            agent.replace_history([
                yuullm.system(system_prompt),
                yuullm.user(f"[context summary]\n{summary}\n\n Please Continue")
            ])
            agent.budget.reset_steps()
            reset_depth -= 1

        await agent.call_llm()
        await agent.call_tools()
```

`Budget` units (`steps`, `tokens`, `usd`) match `BudgetConfig` field names.

### UsageSink

Every `ToolExecutor.run()` call **must** acknowledge `context.sink` — either report a cost or declare it free. Failing to do so raises in `strict` mode and warns otherwise:

```python
# Report a real cost
context.sink.charge("my_service", amount=0.001, unit="usd")

# No direct cost
context.sink.declare_free("cost billed externally")
```

Enable strict mode on the `Runtime`:

```python
runtime = ya.Runtime(eventbus=stage.eventbus, strict=True)
```

## Background tasks

When a tool call exceeds the 300-second timeout, the `Runtime` moves it to background automatically. The `Task.wait()` call returns a synthetic string (`"已移至后台，task_id=..., 完成后自动通知"`) that the LLM sees as the tool result, allowing it to continue.

On completion, `Runtime` delivers a `BackgroundCompletedMessage` to `Stage.mailbox`:

```python
from yuuagents import BackgroundCompletedMessage

msg = await stage.mailbox.recv()
assert isinstance(msg, BackgroundCompletedMessage)
# msg.task_id, msg.agent_id
```

The host injects the result back:

```python
agent.append_message(yuullm.user(f"Background task {task_id} completed: {result}"))
await ya.run_agent_loop(agent, stage.eventbus)
```

## Extending the agent loop

Write your own actor for custom lifecycle behavior. Reuse `create_agent`,
`run_agent_loop`, and the `emit_*` helpers instead of subclassing the sample:

```python
class SupervisedActor:

    def __init__(self, stage: ya.Stage) -> None:
        from yuuagents.tool_backends import ScheduleExecutor

        self.stage = stage
        self._schedule_executor = ScheduleExecutor(stage.mailbox, "cron.sqlite3")
        self.actor_executors = {"schedule": self._schedule_executor}

    def create_agent(self, definition: ya.AgentDefinition) -> ya.Agent:
        return ya.create_agent(
            self.stage,
            definition,
            actor_executors=self.actor_executors,
        )

    async def run_agent_loop(self, agent: ya.Agent) -> None:
        while not agent.done():
            await agent.call_llm()
            # Human-in-the-loop gate
            last = agent.history[-1]
            if not await self._approve(last):
                agent.append_message(yuullm.user("Revise your plan."))
                continue
            await agent.call_tools()
```

## Advanced: Agent loop as a Runtime task

When you wrap your agent loop in a coroutine and submit it via `runtime.submit_task()`,
the Runtime takes over lifecycle management — cancellation, status inspection,
event emission, and owner-based listing all work out of the box.

This is valuable for downstream hosts (web servers, CLI daemons, etc.) because:

- The agent does **not** need to implement its own lifecycle — Runtime already has it.
- Host code can inspect agent status at any time via `runtime.list_tasks(owner=...)`.
- Host code can cancel a stuck agent loop with `runtime.cancel_task(task_id, reason)`.
- Task lifecycle events (`runtime.task_running`, `runtime.task_completed`, etc.)
  flow through the EventBus alongside tool call events.

```python
import asyncio
from yuuagents import Stage, create_agent, AgentDefinition
from yuuagents.core.runtime import Runtime
from yuuagents.core.task import Owner, OwnerType, Task

async def agent_loop(task: Task, stage: Stage, definition: AgentDefinition) -> dict:
    """Your custom agent loop — runs as a Runtime task.

    The ``task`` parameter is injected by Runtime so you can write
    to ``task.stdout`` for real-time observability.
    """
    task.stdout.write("starting agent loop")
    agent = create_agent(stage, definition)
    agent.append_user("Analyze this data and summarize.")
    steps = 0
    while not agent.done:
        msg, store = await agent.step()
        steps += 1
        task.stdout.write(f"step {steps} complete")
    await agent.close()
    return {"status": "completed", "steps": steps}

# Build stage and runtime as usual
stage = Stage(...)
runtime = stage.runtime

# Pass a factory — Runtime calls it with the Task, then runs the coroutine
task = await runtime.submit_task(
    owner=Owner(type=OwnerType.AGENT, id="agent_abc"),
    factory=lambda t: agent_loop(t, stage, definition),
    task_id="agent_loop_001",
    metadata={"agent_name": "researcher"},
)

# Meanwhile, host code can inspect status
info = await runtime.get_task("agent_loop_001")
print(info.status)  # "running"

# Or cancel if it runs too long
await asyncio.sleep(300)
current = await runtime.get_task("agent_loop_001")
if current and current.status in ("pending", "running"):
    await runtime.cancel_task("agent_loop_001", "host timeout")

# Wait for completion
final_task = await runtime.wait_task("agent_loop_001")
print(final_task.result)  # {"status": "completed", "steps": 15}
```

### Owner-based grouping

The owner index lets you find all tasks (tool calls, agent loop, etc.) belonging
to a single agent in one call:

```python
all_agent_tasks = await runtime.list_tasks(
    owner=Owner(type=OwnerType.AGENT, id="agent_abc"),
)
for t in all_agent_tasks:
    print(t.id, t.status, t.info.get("tool_name", "(agent loop)"))
```

## Host tools via @tool

Expose host-side functions as tools by wrapping them in a custom `ToolBackend`/`ToolExecutor`. For simple one-off functions use `@ya.tool` to build a `FunctionTool` and dispatch inside your executor:

```python
@ya.tool(params={"city": "City name"})
async def weather(city: str) -> str:
    return f"{city}: sunny"

class WeatherExecutor:
    _tool = weather  # FunctionTool instance

    def __contains__(self, name: str) -> bool:
        return name == self._tool.name

    async def run(self, tool_name: str, payload: dict, context: ya.ToolExecutionContext) -> str:
        context.sink.declare_free("no cost")
        return await self._tool.run(context=None, arguments=payload)

    async def aclose(self) -> None:
        pass
```

`FunctionTool.spec()` returns an OpenAI-format tool dict suitable for `create_tool_specs`.

## Registry

`Registry[T]` is a typed `dict` subclass with batch-call helpers. `create_agent` uses it to fan out backend calls to only the intersection of registered tool backends and requested tool configs:

```python
registry = ya.Registry({"a": backend_a, "b": backend_b, "c": backend_c})

# Only "a" and "b" — keys present in both registry and caps dict
caps = {"a": {...}, "b": {...}}
executors = registry.select_intersect(caps).broadcast(
    lambda key, backend: backend.create_executor(caps[key])
)
# executors == Registry({"a": executor_a, "b": executor_b})
```

## Type protocols

Implement these protocols to integrate your own infrastructure:

| Protocol | Location | Requires |
|---|---|---|
| `LlmSessionFactory` | `yuuagents.llm_session` | `create_session(history) -> LlmSession` |
| `LlmSession` | `yuuagents.llm_session` | `history`, `append(message)`, `async stream(**options) -> StreamResult` |
| `ToolBackend` | `yuuagents.tool_backends` | `create_executor(tool_config)`, `create_tool_specs(spec_config)` |
| `ToolExecutor` | `yuuagents.tool_backends` | `run(tool_name, payload, context)`, `__contains__`, `aclose()` |
| `Observer` | `yuuagents.eventbus` | `on_event(event: RuntimeEvent) -> object` |
| `PythonSessionLike` | `yuuagents.python_session` | `execute()`, `close()`, `interrupt()` |

---

## Example: autonomous web research

This example shows where the architecture pays off most. The task is open-ended research — finding breaking changes when upgrading a library. Without Python extension functions the LLM has to read raw HTML pages that may be 50 000 tokens each and extract what it needs by itself. With them, the LLM writes a few lines of Python and receives only the content that matters.

### Extension module

```python
# my_app/research_tools.py
"""Web research utilities: search, fetch, extract, score."""

import re
import httpx
from markdownify import markdownify

__all__ = ["search", "fetch_text", "extract_sections", "keyword_score"]


async def search(query: str, n: int = 10) -> list[dict]:
    """Run a web search query. Returns [{url, title, snippet}] sorted by relevance."""
    async with httpx.AsyncClient() as c:
        r = await c.get("https://api.search.example/v1", params={"q": query, "n": n})
        return r.json()["results"]


async def fetch_text(url: str, timeout: float = 15.0) -> str:
    """Fetch a URL and return its content as plain Markdown. Strips nav/footer boilerplate."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as c:
        r = await c.get(url, headers={"User-Agent": "research-bot/1.0"})
        r.raise_for_status()
        md = markdownify(r.text, strip=["script", "style", "nav", "footer"])
        return _collapse_blank_lines(md)


def extract_sections(text: str, keywords: list[str], context_lines: int = 4) -> list[str]:
    """Extract paragraphs from text that contain any of the given keywords.

    Returns deduplicated paragraph strings.  Each result includes context_lines
    of surrounding lines so the LLM has enough context to evaluate relevance.
    """
    lines = text.splitlines()
    hits: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if any(kw.lower() in line.lower() for kw in keywords):
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            hits.append((start, end))
    merged = _merge_ranges(hits)
    seen: set[str] = set()
    out: list[str] = []
    for start, end in merged:
        block = "\n".join(lines[start:end]).strip()
        if block and block not in seen:
            seen.add(block)
            out.append(block)
    return out


def keyword_score(text: str, keywords: list[str]) -> float:
    """Return a 0–1 relevance score based on keyword density."""
    if not text:
        return 0.0
    words = re.findall(r"\w+", text.lower())
    if not words:
        return 0.0
    hits = sum(1 for w in words if any(kw.lower() in w for kw in keywords))
    return min(1.0, hits / max(1, len(words)) * 20)


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    out = [ranges[0]]
    for start, end in sorted(ranges[1:]):
        if start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out
```

### Configuration

```toml
[tools.ipykernel]
imports = [{module = "my_app.research_tools", alias = "research"}]
expand_functions = ["research.*"]

[tools.ipykernel.spec]
level = "detail"
```

### What the agent actually does

The LLM receives the task: *"What breaks when upgrading httpx from 0.23 to 0.27?"*

**Turn 1 — broad search to find candidate pages:**

```python
import research

results = await research.search("httpx migration 0.23 to 0.27 breaking changes")
for r in results:
    score = research.keyword_score(r["snippet"], ["breaking", "migration", "deprecated", "removed"])
    print(f"{score:.2f}  {r['title'][:60]:60s}  {r['url']}")
```

Output the LLM sees (~80 tokens):
```
0.91  Changelog — encode/httpx                                 https://github.com/encode/httpx/blob/master/CHANGELOG.md
0.74  Migration guide · encode/httpx Wiki                     https://github.com/encode/httpx/wiki/0.x-Migration-Guide
0.12  How to handle timeouts in httpx — Stack Overflow         https://stackoverflow.com/questions/71234...
0.05  httpx vs requests performance comparison — blog post     https://blog.example.com/httpx-vs-requests
```

The LLM does not read any full page yet — it only sees scores and titles.

**Turn 2 — fetch and narrow the two high-scoring URLs:**

```python
changelog = await research.fetch_text("https://github.com/encode/httpx/blob/master/CHANGELOG.md")
sections = research.extract_sections(
    changelog,
    keywords=["breaking", "removed", "deprecated", "renamed", "no longer"],
    context_lines=6,
)
for i, s in enumerate(sections):
    print(f"\n--- section {i+1} ---\n{s}")
```

`fetch_text` returns the full changelog (~18 000 tokens of markdown). `extract_sections` filters it to the 6–8 paragraphs that mention breaking changes. The LLM sees ~600 tokens of distilled content instead of 18 000.

**Turn 3 — targeted re-search for an ambiguous item found in the sections:**

A section mentioned `transport` was refactored but gave no detail. The LLM drills down:

```python
results = await research.search('"httpx" "transport" "0.2" breaking change site:github.com')
transport_page = await research.fetch_text(results[0]["url"])
hits = research.extract_sections(transport_page, ["AsyncHTTPTransport", "HTTPTransport", "mount"])
for h in hits:
    print(h)
```

Again, only the relevant paragraphs surface — the LLM never reads the entire page.

**Turn 4 — synthesize into a migration checklist:**

Having collected ~1 500 tokens of dense, pre-filtered content across three steps, the LLM synthesises:

```
Breaking changes when upgrading httpx 0.23 → 0.27:

1. Timeout is now a Timeout object, not a float. Replace:
     httpx.get(url, timeout=5.0)
   with:
     httpx.get(url, timeout=httpx.Timeout(5.0, connect=2.0))

2. HTTPTransport and AsyncHTTPTransport replace the old transport API.
   Custom transports must now inherit from these base classes.

3. auth= parameter no longer accepts a plain tuple on async clients.
   Use httpx.BasicAuth("user", "pass") instead.

4. Response.text now raises DecodeError instead of returning ''.
```

### Why this matters

Without Python extension functions the agent's only option is to call a generic `web_search` tool that returns raw snippets, or a `fetch_url` tool that returns entire pages. The entire changelog and two wiki pages, dumped into the context, would consume ~40 000 tokens — much of it irrelevant release notes and formatting noise. The LLM might hallucinate items it missed, or simply fail to read that far.

With extension functions the LLM treats Python as a **programmable filter**:

| Without extensions | With extensions |
|---|---|
| LLM reads raw search snippets — 10 × 200 tokens of noise | LLM reads scored table — 80 tokens, picks 2 URLs |
| LLM receives full changelog page dump — ~18 000 tokens | `extract_sections` delivers 8 relevant paragraphs — ~600 tokens |
| LLM has to hold context across multiple large pages | Each turn produces a compact summary; prior pages are never in context |
| Ambiguous items require a new user message to clarify | LLM issues a targeted re-search in the same turn |
| Total context consumed: ~45 000 tokens | Total context consumed: ~2 500 tokens |

The LLM is never a passive reader. It writes Python to **ask exactly the question it needs answered**, then acts on the answer.
