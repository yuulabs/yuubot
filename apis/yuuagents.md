# yuuagents API 文档

## 概述

yuuagents 是一个最小 Agent 框架，核心公式：**Persona + Tools + LLM → Flow/Agent**。它基于 Flow（可观测、可寻址、可中断的执行单元）和 Agent（在 Flow 之上组合 LLM 行为）构建，通过 Session 向宿主暴露稳定接口。

核心设计：
- **Flow/Agent** — Flow 是最小执行单元（stem + mailbox + cancel），Agent 组合 Flow 与 LLM 行为
- **Session** — 宿主侧的薄包装，提供 start/send/resume/step_iter/snapshot/kill 接口
- **AgentConfig** — 不可变配置（frozen attrs）
- **AgentState** — 快照结构体（msgspec Struct），用于持久化和恢复
- **依赖注入** — `AgentContext` 通过 `yuutools.depends()` 注入到工具中
- **快照持久化** — 宿主在 step 边界调用 snapshot()，写入 SQLite

## 核心类型

### AgentConfig

不可变配置，`@attrs.define(frozen=True)`。

```python
@define(frozen=True, init=False)
class AgentConfig:
    agent_id: str
    system: str                    # 系统提示（persona/system_prompt 为兼容别名）
    tools: yt.ToolManager
    llm: yuullm.YLLMClient
    max_steps: int = 0             # 0 = unlimited
    soft_timeout: float | None = None
    silence_timeout: float | None = None
    tool_batch_timeout: float = 0
```

### AgentState

快照结构体，用于持久化和恢复。

```python
class AgentState(msgspec.Struct, frozen=True):
    messages: tuple[yuullm.Message, ...]
    total_usage: yuullm.Usage | None
    total_cost_usd: float
    rounds: int
    conversation_id: str | None    # UUID hex or None
```

- 每个快照是完整状态，不是增量
- 所有 tool_call 均有配对的 tool result
- 恢复时通过 `initial_messages` 构建新 Agent

### AgentContext

运行时上下文，通过 `yuutools.depends()` 注入到工具中。

```python
@define
class AgentContext:
    task_id: str
    agent_id: str
    workdir: str
    docker_container: str
    delegate_depth: int = 0
    manager: DelegateManager | None = None
    docker: DockerExecutor | None = None
    tavily_api_key: str = ""
    subprocess_env: dict | None = None
    addon_context: object | None = None
    session: Session | None = None
    current_run_id: str = ""
    current_flow: Flow | None = None
```

### StepResult

Agent.steps() 每轮 yield 的结果。

```python
@attrs.define(frozen=True)
class StepResult:
    done: bool       # True = LLM 无 tool calls，自然结束
    tokens: int = 0  # 累计 total tokens
    rounds: int = 0  # 累计 LLM 轮数
```

### AgentStatus

```python
class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    BLOCKED_ON_INPUT = "blocked_on_input"
    CANCELLED = "cancelled"
```

### AgentInfo

API 返回的 Agent 摘要。

```python
class AgentInfo(msgspec.Struct, frozen=True, kw_only=True):
    task_id: str
    agent_id: str
    persona: str
    task: str
    status: str
    created_at: str
    last_assistant_message: str = ""
    pending_input_prompt: str = ""
    steps: int = 0
    total_tokens: int = 0
    last_usage: yuullm.Usage | None = None
    total_usage: yuullm.Usage | None = None
    last_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    error: ErrorInfo | None = None
```

## Session（宿主主接口）

Session 是 yuubot 等宿主与 yuuagents 交互的主抽象。

```python
@define
class Session:
    config: AgentConfig
    context: AgentContext
    task: str
    history: list[yuullm.Message]
    status: AgentStatus
    error: ErrorInfo | None
    stop_reason: str
```

### 方法

| 方法 | 说明 |
|------|------|
| `start(task)` | 创建底层 FlowAgent 并队列任务 |
| `send(content, *, defer_tools=False)` | 向运行中的 agent 发送消息 |
| `resume(task, *, history, conversation_id, system)` | 从历史恢复后发送新任务 |
| `cancel()` | 取消运行中的 agent flow |
| `step_iter()` | 宿主驱动的 step 迭代，yield StepResult |
| `snapshot(*, as_interrupted=False)` | 获取当前 AgentState 快照 |
| `kill()` | 取消所有后台任务，合成中断结果 |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `steps` | `int` | 累计步数 |
| `total_tokens` | `int` | 累计 token 数 |
| `last_usage` | `Usage \| None` | 最近一次 LLM 调用的 usage |
| `total_usage` | `Usage \| None` | 累计 usage |
| `last_cost_usd` | `float` | 最近一次 LLM 调用的费用 |
| `total_cost_usd` | `float` | 累计费用 |
| `last_input_tokens` | `int` | 最近一次输入 token 数 |
| `has_pending_background` | `bool` | 是否有后台任务运行中 |
| `conversation_id` | `UUID \| None` | 对话 ID |

### 典型使用（宿主侧）

```python
session = Session(config=agent_config, context=agent_ctx)
session.start(task="帮我总结这篇文章")

async for step in session.step_iter():
    if not session.has_pending_background:
        state = await session.snapshot()
        # persist state to DB
```

## 内置 Tools

所有内置工具使用 `@yt.tool()` 定义，通过 `yt.depends()` 注入运行时依赖。

| 工具名 | 参数 | 主要用途 |
|--------|------|----------|
| `execute_bash` | `command`, `timeout` | 在 Docker 容器内执行命令 |
| `read_file` | `path`, `start_line`, `end_line` | 读取容器内文件 |
| `write_file` | `path`, `patch` | 通过 unified diff 修改文件 |
| `delete_file` | `path` | 删除容器内文件 |
| `web_search` | `query`, `max_results` | Tavily 搜索 |
| `delegate` | `agent`, `context`, `task`, `tools` | 委托子 agent |
| `view_image` | `path` | 查看图片 |

## 公开 API

```python
from yuuagents import (
    AgentConfig, AgentState, AgentContext, Session,
    AgentStatus, AgentInfo, StepResult, TaskRequest,
    tool, Tool, ToolManager, depends,
)
```
