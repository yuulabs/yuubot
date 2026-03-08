# yuuagents API 文档

## 概述

yuuagents 是一个最小 Agent 框架，核心公式：**Persona + Tools + LLM → Loop**。它提供从配置加载、Agent 定义、运行循环到持久化的完整链路，同时通过依赖注入和 Docker 隔离保证工具执行的安全性。

核心设计：
- **三件套** — `AgentConfig`（不可变配置） + `AgentState`（可变状态） + `Agent`（组合）
- **运行循环** — `run()` 函数驱动 LLM 调用 → 工具执行 → 结果回传的循环
- **依赖注入** — `AgentContext` 携带运行时上下文，工具通过 `yuutools.depends()` 获取
- **Docker 隔离** — bash 命令和文件操作在 Docker 容器内执行
- **Skills 系统** — 扫描 SKILL.md 发现技能并注入系统提示

## 安装 & 快速开始

```python
import yuuagents
from yuuagents import Agent, AgentContext, AgentConfig, run_agent
from yuuagents.tools import execute_bash, read_file, write_file

# 1. 初始化（幂等，可重复调用）
config = await yuuagents.init.setup("config.yaml")

# 2. 创建 Agent
agent_config = AgentConfig(
    task_id="task-001",
    agent_id="coder",
    persona="你是一个 Python 开发者。",
    tools=yt.ToolManager([execute_bash, read_file, write_file]),
    llm=my_llm_client,
    prompt_builder=SimplePromptBuilder().add_section("你是一个 Python 开发者。"),
)
agent = Agent(config=agent_config)

# 3. 创建上下文
ctx = AgentContext(
    task_id="task-001",
    agent_id="coder",
    workdir="/workspace",
    docker_container="my-container-id",
)

# 4. 运行
await run_agent(agent, task="写一个 hello world 程序", ctx=ctx)
```

## 初始化 (`init.setup()`)

```python
async def setup(config: str | Path | Config) -> Config
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `config` | `str \| Path \| Config` | YAML 配置文件路径或 Config 对象 |

**返回值：** `Config` — 解析后的配置对象

**幂等性：** 可安全地多次调用，已完成的步骤会被跳过。

**执行步骤：**

1. 创建目录 — `~/.yagents/`、`~/.yagents/skills/`、`~/.yagents/dockers/` 及所有配置的 skill 路径
2. 写入配置 — 将解析后的 config 写入 `~/.yagents/config.yaml`
3. 初始化数据库 — 创建 TaskPersistence 表
4. 确保 Docker 镜像 — 构建 `yuuagents-runtime:*` 或拉取自定义镜像
5. 启动守护进程 — 若未运行，通过 `yagents up -d` 启动

## Agent 核心三件套

### AgentConfig

不可变配置，`@attrs.define(frozen=True)`。

```python
@define(frozen=True)
class AgentConfig:
    task_id: str
    agent_id: str
    persona: str
    tools: yt.ToolManager
    llm: yuullm.YLLMClient
    prompt_builder: PromptBuilder
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | 是 | 任务标识符 |
| `agent_id` | `str` | 是 | Agent 标识符 |
| `persona` | `str` | 是 | 系统角色定义 |
| `tools` | `yt.ToolManager` | 是 | 可用工具集 |
| `llm` | `yuullm.YLLMClient` | 是 | LLM 客户端 |
| `prompt_builder` | `PromptBuilder` | 是 | 系统提示构建器 |

### AgentState

可变运行状态，`@attrs.define`。

```python
@define
class AgentState:
    task: str = ""
    history: list[yuullm.Message] = field(factory=list)
    status: AgentStatus = AgentStatus.IDLE
    error: ErrorInfo | None = None
    pending_input_prompt: str = ""
    steps: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: datetime = field(factory=lambda: datetime.now(timezone.utc))
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task` | `str` | `""` | 当前任务描述 |
| `history` | `list[yuullm.Message]` | `[]` | 会话历史 |
| `status` | `AgentStatus` | `IDLE` | 当前状态 |
| `error` | `ErrorInfo \| None` | `None` | 错误信息 |
| `pending_input_prompt` | `str` | `""` | 等待用户输入时的提示 |
| `steps` | `int` | `0` | 已执行的 LLM 步骤数 |
| `total_tokens` | `int` | `0` | 总 token 用量 |
| `total_cost_usd` | `float` | `0.0` | 总费用（USD） |
| `created_at` | `datetime` | `now(UTC)` | 创建时间 |

### Agent

组合 AgentConfig 和 AgentState，`@attrs.define`。

```python
@define
class Agent:
    config: AgentConfig
    state: AgentState = field(factory=AgentState)
```

**属性（代理到 config/state）：**

| 属性 | 来源 | 读/写 |
|------|------|-------|
| `task_id` | config | 只读 |
| `agent_id` | config | 只读 |
| `persona` | config | 只读 |
| `tools` | config | 只读 |
| `llm` | config | 只读 |
| `task` | state | 只读 |
| `history` | state | 只读 |
| `status` | state | 读写 |
| `steps` | state | 读写 |
| `total_tokens` | state | 读写 |
| `total_cost_usd` | state | 读写 |
| `created_at` | state | 只读 |
| `error` | state | 只读 |
| `full_system_prompt` | prompt_builder.build() | 只读 |

**方法：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `setup()` | `setup(task: str) -> None` | 初始化：设状态为 RUNNING，创建带系统提示和用户消息的 history |
| `fail()` | `fail(exc: Exception) -> None` | 标记失败：设状态为 ERROR，捕获完整 traceback、异常类型、时间戳 |
| `done()` | `done() -> bool` | 是否终态（DONE、ERROR 或 CANCELLED） |

## AgentContext (依赖注入)

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
    state: AgentState | None = None
    input_queue: asyncio.Queue[str] = field(factory=asyncio.Queue)
    tavily_api_key: str = ""
```

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | `str` | 任务 ID |
| `agent_id` | `str` | Agent ID |
| `workdir` | `str` | 工作目录路径 |
| `docker_container` | `str` | Docker 容器 ID/名称 |

### 可选字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `delegate_depth` | `int` | `0` | 当前委托深度（最大 3） |
| `manager` | `DelegateManager \| None` | `None` | 委托管理器（delegate tool 使用） |
| `docker` | `DockerExecutor \| None` | `None` | Docker 执行器 |
| `state` | `AgentState \| None` | `None` | Agent 状态（由 loop 设置，user_input tool 使用） |
| `input_queue` | `asyncio.Queue[str]` | `Queue()` | 用户输入队列（user_input tool 使用） |
| `tavily_api_key` | `str` | `""` | Tavily API 密钥（web_search tool 使用） |

## PromptBuilder

### PromptBuilder 协议

```python
class PromptBuilder(Protocol):
    def build(self) -> str: ...
```

单方法协议，返回完整的系统提示字符串。

### SimplePromptBuilder 实现

```python
class SimplePromptBuilder:
    def __init__(self) -> None:
        self._sections: list[str] = []

    def add_section(self, section: str) -> "SimplePromptBuilder":
        """添加非空段落，返回 self 支持链式调用。"""
        ...

    def build(self) -> str:
        """用 "\\n\\n" 连接所有段落。"""
        ...
```

**使用示例：**

```python
builder = (
    SimplePromptBuilder()
    .add_section("你是一个 Python 开发者。")
    .add_section("可用工具：execute_bash, read_file, write_file")
    .add_section(skills_xml)  # 由 skills.render() 生成
)
prompt = builder.build()
```

## 运行循环 `run()`

```python
async def run(
    agent: Agent,
    task: str,
    ctx: AgentContext,
    *,
    recorder: TaskRecorder | None = None,
    resume: bool = False,
) -> None
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `agent` | `Agent` | 是 | - | Agent 实例 |
| `task` | `str` | 是 | - | 任务描述 / 首条用户消息 |
| `ctx` | `AgentContext` | 是 | - | 运行时上下文 |
| `recorder` | `TaskRecorder \| None` | 否 | `None` | 持久化记录器 |
| `resume` | `bool` | 否 | `False` | 从检查点恢复而非全新启动 |

**导入：** `from yuuagents import run_agent`（`run_agent` 是 `loop.run` 的别名）

### 行为流程

```
初始化
  ├─ 非恢复模式：agent.setup(task)
  └─ 设置 trace context

主循环 (while not agent.done())
  │
  ├─ LLM 调用
  │   ├─ 流式读取响应
  │   ├─ 分离文本 / 工具调用
  │   ├─ 更新 token 和 cost
  │   ├─ 步数 +1
  │   └─ 持久化 LLM checkpoint
  │
  ├─ 无工具调用？→ status = DONE → 退出循环
  │
  └─ 工具执行
      ├─ 解析参数 JSON
      ├─ bind(ctx) 注入依赖
      ├─ 并行执行所有工具
      ├─ 结果追加到 history
      └─ 持久化 tool checkpoint

异常处理
  └─ agent.fail(exc) → 重新抛出
```

**终态判定：**
- `DONE` — LLM 未返回任何工具调用
- `ERROR` — 执行过程中发生异常
- `CANCELLED` — 外部取消

## 内置 Tools 参考

所有内置工具使用 `@yt.tool()` 装饰器定义，通过 `yt.depends()` 注入运行时依赖。

### execute_bash

在 Docker 容器中执行 bash 命令。

```python
@yt.tool(params={"command": "...", "timeout": "..."})
async def execute_bash(
    command: str,
    timeout: int = 120,
    session_id: str = yt.depends(lambda ctx: ctx.task_id),
    container: str = yt.depends(lambda ctx: ctx.docker_container),
    docker: DockerExecutor = yt.depends(lambda ctx: ctx.docker),
) -> str
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `command` | `str` | 必填 | Bash 命令 |
| `timeout` | `int` | `120` | 超时秒数，范围 1-600 |

| 依赖注入 | 来源 |
|----------|------|
| `session_id` | `ctx.task_id` |
| `container` | `ctx.docker_container` |
| `docker` | `ctx.docker` |

**行为：** 在持久终端会话中执行命令，工作目录和环境变量跨调用保留。

### execute_skill_cli

在宿主机上执行 skill 提供的 CLI 命令。

```python
@yt.tool(params={"command": "...", "timeout": "..."})
async def execute_skill_cli(
    command: str,
    timeout: int = 300,
) -> str
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `command` | `str` | 必填 | CLI 命令 |
| `timeout` | `int` | `300` | 超时秒数，范围 1-3600 |

**安全限制：**
- 禁止的程序：`bash`, `sh`, `python`, `rm`, `kill`, `sudo` 等
- 禁止的 shell 控制符：`;`, `|`, `&`, `&&`, `||`, `>`, `>>`, `<`, `<<`
- 禁止 shell 扩展：`$(...)` 和反引号
- 违反限制时抛出 `ValueError`

**行为：** 在宿主机用户家目录执行，stdin 为 DEVNULL，返回包含退出码的输出。

### read_file

读取 Docker 容器中的文件。

```python
@yt.tool(params={"path": "..."})
async def read_file(
    path: str,
    container: str = yt.depends(lambda ctx: ctx.docker_container),
    docker: DockerExecutor = yt.depends(lambda ctx: ctx.docker),
) -> str
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `str` | 绝对文件路径 |

| 依赖注入 | 来源 |
|----------|------|
| `container` | `ctx.docker_container` |
| `docker` | `ctx.docker` |

**行为：** 在容器中执行 `cat <path>`，超时 30 秒。

### write_file

通过 unified diff patch 修改 Docker 容器中的文件。

```python
@yt.tool(params={"path": "...", "patch": "..."})
async def write_file(
    path: str,
    patch: str,
    container: str = yt.depends(lambda ctx: ctx.docker_container),
    docker: DockerExecutor = yt.depends(lambda ctx: ctx.docker),
) -> str
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `str` | 绝对文件路径 |
| `patch` | `str` | Unified diff 格式的补丁 |

| 依赖注入 | 来源 |
|----------|------|
| `container` | `ctx.docker_container` |
| `docker` | `ctx.docker` |

**行为：** 将 patch base64 编码后传递给 `yagents-apply-patch` 工具，返回 diff 摘要。patch 应用失败时抛出 `AssertionError`。

### delete_file

删除 Docker 容器中的文件。

```python
@yt.tool(params={"path": "..."})
async def delete_file(
    path: str,
    container: str = yt.depends(lambda ctx: ctx.docker_container),
    docker: DockerExecutor = yt.depends(lambda ctx: ctx.docker),
) -> str
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `str` | 绝对文件路径 |

| 依赖注入 | 来源 |
|----------|------|
| `container` | `ctx.docker_container` |
| `docker` | `ctx.docker` |

**行为：** 在容器中执行 `rm -f <path>`，超时 30 秒。

### user_input

请求用户输入，阻塞直到收到回复。

```python
@yt.tool(params={"prompt": "..."})
async def user_input(
    prompt: str,
    state: AgentState = yt.depends(lambda ctx: ctx.state),
    input_queue: asyncio.Queue[str] = yt.depends(lambda ctx: ctx.input_queue),
) -> str
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `prompt` | `str` | 向用户展示的提示信息 |

| 依赖注入 | 来源 |
|----------|------|
| `state` | `ctx.state` |
| `input_queue` | `ctx.input_queue` |

**行为：**
1. 设置 `state.pending_input_prompt = prompt`
2. 设置 `state.status = AgentStatus.BLOCKED_ON_INPUT`
3. 阻塞等待 `input_queue.get()`
4. 收到输入后清除 prompt 并恢复 RUNNING 状态
5. 返回用户输入

### web_search

使用 Tavily API 搜索网页。

```python
@yt.tool(params={"query": "...", "max_results": "..."})
async def web_search(
    query: str,
    max_results: int = 5,
    api_key: str = yt.depends(lambda ctx: ctx.tavily_api_key),
) -> str
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | `str` | 必填 | 搜索关键词 |
| `max_results` | `int` | `5` | 最大结果数，范围 1-10 |

| 依赖注入 | 来源 |
|----------|------|
| `api_key` | `ctx.tavily_api_key` |

**行为：** 调用 `tavily.AsyncTavilyClient` 搜索，返回格式化的编号结果列表。

### delegate

委托任务给其他 Agent。

```python
@yt.tool(params={"agent": "...", "context": "...", "task": "...", "tools": "..."})
async def delegate(
    agent: str,
    context: str,
    task: str,
    tools: list[str] | None = None,
    manager: DelegateManager | None = yt.depends(lambda ctx: ctx.manager),
    caller_agent: str = yt.depends(lambda ctx: ctx.agent_id),
    delegate_depth: int = yt.depends(lambda ctx: ctx.delegate_depth),
) -> str
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent` | `str` | 必填 | 目标 Agent 名称 |
| `context` | `str` | 必填 | 任务上下文 |
| `task` | `str` | 必填 | 具体任务描述 |
| `tools` | `list[str] \| None` | `None` | 覆盖工具列表 |

| 依赖注入 | 来源 |
|----------|------|
| `manager` | `ctx.manager` |
| `caller_agent` | `ctx.agent_id` |
| `delegate_depth` | `ctx.delegate_depth` |

**行为：**
1. 验证所有字符串参数非空
2. 检查委托深度 < 3（否则抛 `DelegateDepthExceededError`）
3. 组合 context 和 task 为首条用户消息
4. 调用 `manager.delegate()`，深度 +1
5. 返回被委托 Agent 的最终文本回复

## Skills 系统

### scan() 函数

```python
def scan(paths: list[str]) -> list[SkillInfo]
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `paths` | `list[str]` | 要扫描的目录列表 |

**返回值：** `list[SkillInfo]` — 已排序的 skill 信息列表

**行为：** 遍历每个路径的子目录，查找并解析 `SKILL.md` 文件的 YAML frontmatter。

### render() 函数

```python
def render(skills: list[SkillInfo]) -> str
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `skills` | `list[SkillInfo]` | SkillInfo 列表 |

**返回值：** XML 格式字符串，空列表返回 `""`

**输出格式：**

```xml
<available_skills>
<skill>
<name>calculator</name>
<description>数学计算工具</description>
<location>/home/user/.yagents/skills/calculator/SKILL.md</location>
</skill>
</available_skills>
```

### SKILL.md 格式

YAML frontmatter 包含 `name` 和 `description` 属性，由 `skills_ref.read_properties()` 解析。

### 目录结构

```
~/.yagents/skills/
├── calculator/
│   ├── SKILL.md        # name + description
│   └── ...
├── web_scraper/
│   ├── SKILL.md
│   └── ...
```

## 配置类参考

### Config

顶层配置，`msgspec.Struct(kw_only=True)`。

```python
class Config(msgspec.Struct, kw_only=True):
    daemon: DaemonConfig = DaemonConfig()
    db: DbConfig = DbConfig()
    yuutrace: YuuTraceConfig = YuuTraceConfig()
    docker: DockerConfig = DockerConfig()
    skills: SkillsConfig = SkillsConfig()
    tavily: TavilyConfig = TavilyConfig()
    providers: dict[str, ProviderConfig] = {}
    agents: dict[str, AgentEntry] = {}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `daemon` | `DaemonConfig` | `DaemonConfig()` | 守护进程配置 |
| `db` | `DbConfig` | `DbConfig()` | 数据库配置 |
| `yuutrace` | `YuuTraceConfig` | `YuuTraceConfig()` | Trace 配置 |
| `docker` | `DockerConfig` | `DockerConfig()` | Docker 配置 |
| `skills` | `SkillsConfig` | `SkillsConfig()` | Skills 配置 |
| `tavily` | `TavilyConfig` | `TavilyConfig()` | Tavily 配置 |
| `providers` | `dict[str, ProviderConfig]` | `{}` | LLM 供应商映射 |
| `agents` | `dict[str, AgentEntry]` | `{}` | Agent 定义映射 |

**属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `socket_path` | `Path` | 展开后的守护进程 socket 路径 |
| `db_url` | `str` | SQLAlchemy 数据库 URL |
| `sqlite_path` | `Path \| None` | SQLite 文件路径（非 sqlite 时为 None） |

**方法：**

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `validate()` | `list[str]` | 验证错误列表（空表示通过） |

### ProviderConfig

```python
class ProviderConfig(msgspec.Struct, kw_only=True):
    api_type: str = "openai-chat-completion"
    api_key_env: str = "OPENAI_API_KEY"
    default_model: str = "gpt-4o"
    base_url: str = ""
    organization: str = ""
    pricing: list[PricingEntry] = []
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_type` | `str` | `"openai-chat-completion"` | `"openai-chat-completion"` / `"anthropic-messages"` |
| `api_key_env` | `str` | `"OPENAI_API_KEY"` | API 密钥环境变量名 |
| `default_model` | `str` | `"gpt-4o"` | 默认模型 |
| `base_url` | `str` | `""` | 自定义端点 URL |
| `organization` | `str` | `""` | OpenAI 组织 ID |
| `pricing` | `list[PricingEntry]` | `[]` | 自定义定价 |

### PricingEntry

```python
class PricingEntry(msgspec.Struct, kw_only=True):
    model: str                     # 必填
    input_mtok: float = 0.0        # USD / 百万输入 token
    output_mtok: float = 0.0       # USD / 百万输出 token
    cache_read_mtok: float = 0.0
    cache_write_mtok: float = 0.0
```

### AgentEntry

```python
class AgentEntry(msgspec.Struct, kw_only=True):
    description: str               # 必填
    provider: str = ""
    model: str = ""
    persona: str = ""
    subagents: list[str] = []
    tools: list[str] = []
    skills: list[str] = []
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `description` | `str` | 必填 | Agent 描述 |
| `provider` | `str` | `""` | 引用 providers 中的名称 |
| `model` | `str` | `""` | 覆盖 provider 默认模型 |
| `persona` | `str` | `""` | 系统提示 |
| `subagents` | `list[str]` | `[]` | 可委托的 Agent 列表（`"*"` 表示所有） |
| `tools` | `list[str]` | `[]` | 工具名称列表 |
| `skills` | `list[str]` | `[]` | Skill 名称列表 |

### 其他配置子类

| 类 | 字段 | 默认值 |
|----|------|--------|
| `DaemonConfig` | `socket: str` | `"~/.yagents/yagents.sock"` |
| | `log_level: str` | `"info"` |
| `DockerConfig` | `image: str` | `"yuuagents-runtime:latest"` |
| `SkillsConfig` | `paths: list[str]` | `["~/.yagents/skills"]` |
| `TavilyConfig` | `api_key_env: str` | `"TAVILY_API_KEY"` |
| `DbConfig` | `url: str` | `"sqlite+aiosqlite:///~/.yagents/tasks.sqlite3"` |
| `YuuTraceConfig` | `db_path: str` | `"~/.yagents/traces.db"` |
| | `ui_port: int` | `8080` |
| | `server_port: int` | `4318` |

## 配置加载函数

### load()

```python
def load(path: str | Path | None = None) -> Config
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `path` | `str \| Path \| None` | `None` | YAML 路径，默认 `~/.yagents/config.yaml` |

文件不存在或为空时返回全默认 `Config()`。

### load_merged()

```python
def load_merged(
    base_path: str | Path,
    overrides_path: str | Path | None = None,
) -> Config
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `base_path` | `str \| Path` | 是 | 基础配置文件路径 |
| `overrides_path` | `str \| Path \| None` | 否 | 覆盖配置文件路径 |

**合并规则：** dict 值递归合并，其他类型覆盖替换。基础文件不存在时抛 `FileNotFoundError`。

## 类型定义

### AgentStatus 枚举

```python
class AgentStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    BLOCKED_ON_INPUT = "blocked_on_input"
    CANCELLED = "cancelled"
```

### AgentInfo

API 返回的 Agent 摘要信息。

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
    total_cost_usd: float = 0.0
    error: ErrorInfo | None = None
```

### TaskRequest

创建任务的请求载荷。

```python
class TaskRequest(msgspec.Struct, frozen=True, kw_only=True):
    agent: str = "main"
    persona: str = ""
    task: str              # 必填
    tools: list[str] = []
    skills: list[str] = []
    model: str = ""
    container: str = ""
    image: str = ""
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent` | `str` | `"main"` | Agent 配置名称 |
| `persona` | `str` | `""` | 覆盖系统提示 |
| `task` | `str` | 必填 | 任务描述 |
| `tools` | `list[str]` | `[]` | 覆盖工具列表 |
| `skills` | `list[str]` | `[]` | 覆盖 skill 列表 |
| `model` | `str` | `""` | 覆盖模型 |
| `container` | `str` | `""` | 指定容器 |
| `image` | `str` | `""` | 指定 Docker 镜像 |

### SkillInfo

```python
class SkillInfo(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    description: str
    location: str   # SKILL.md 文件路径
```

### ErrorInfo

```python
class ErrorInfo(msgspec.Struct, frozen=True, kw_only=True):
    message: str       # 完整错误消息（含 traceback）
    error_type: str    # 异常类名
    timestamp: datetime
```

## 持久化（高级）

### TaskPersistence

基于 SQLAlchemy async 的任务持久化。

```python
@define
class TaskPersistence:
    db_url: str
```

**生命周期方法：**

| 方法 | 说明 |
|------|------|
| `start()` | 创建 engine、sessionmaker 并初始化表 |
| `stop()` | 关闭 engine |

**数据操作：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `create_task()` | `async def create_task(*, task_id, agent_id, persona, task, system_prompt, model, tools, docker_container, created_at)` | 插入任务记录 |
| `update_task_terminal()` | `async def update_task_terminal(*, task_id, status, error_json=None)` | 更新终态和错误信息 |
| `list_tasks()` | `async def list_tasks() -> list[AgentInfo]` | 列出所有任务 |
| `get_task_row()` | `async def get_task_row(task_id) -> TaskRow \| None` | 获取任务行 |
| `load_history()` | `async def load_history(task_id) -> list[Any]` | 从检查点重建历史 |
| `pending_input_prompt()` | `async def pending_input_prompt(task_id) -> str \| None` | 获取等待输入的提示 |
| `load_unfinished()` | `async def load_unfinished() -> list[RestoredTask]` | 加载所有未完成任务 |
| `recover_pending_tools()` | `async def recover_pending_tools(task_id) -> bool` | 标记中断的工具为失败 |

### TaskRecorder

轻量记录器，封装 TaskWriter 的异步写入。

```python
@define(frozen=True)
class TaskRecorder:
    task_id: str
    writer: TaskWriter
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `record_llm()` | `async def record_llm(*, turn, history_append, tool_calls)` | 记录 LLM 生成 |
| `record_user()` | `async def record_user(*, turn, message)` | 记录用户消息 |
| `record_tool()` | `async def record_tool(*, turn, results)` | 记录工具执行结果 |
