# 重写目标与迁移指南

## 迁移映射

| 当前 | 新设计 | 说明 |
|------|--------|------|
| `Engine` | `Actor` + `Runtime` | Engine 拆成两层：Actor 管生命周期，Runtime 管 ToolExecutor 路由和后台任务 |
| `AgentDefinition` | `ToolDefinition` + `PromptDefinition` | 能力需求（资源分配）与 Prompt 结构分离 |
| `Engine.tools: list[Tool]` 全局注册表 | `ToolExecutor`（per-ToolBackend） | 工具不再全局注册，由各 ToolBackend 的 ToolExecutor 持有；Runtime 负责路由 `tool_name → executor` |
| `Engine.python: PythonRuntime` | `IpykernelToolBackend`（key=`"ipykernel"`） | Python 能力成为标准 ToolBackend，配置走 `tools["ipykernel"]` |
| `Engine.observers: list[RuntimeObserver]` | `EventBus.subscribe(observer)` | 从列表改为显式发布订阅；事件名不变 |
| `BillingSink` | `UsageSink.charge()` | 从事件驱动改为 per-task inline 上报；Budget 订阅 `runtime.usage_reported` 做累加 |
| `Engine._background_tasks` / `_BackgroundTaskState` | `Runtime.submit_bg()` + `BackgroundTask` | 后台任务托管职责移入 Runtime，ToolExecutor 返回 `BackgroundTask` 而非 `BackgroundTaskHandle` |
| `Agent.steps()` AsyncIterator | `Agent.call_llm()` / `Agent.call_tools()` | Agent 暴露精确语义；Actor 在 `run_agent_loop()` 里组合成循环 |
| `Engine.create_agent()` | `Actor.create_agent()` | 职责不变，内部走 `tool_backends.each.create_executor()` + `tool_backends.each.create_tool_specs()` |
| `AgentDefinition.tools: tuple[str,...]` 白名单过滤 | `PromptDefinition` per-provider `level` 字段 | 工具可见性改由 prompt level 控制（`"type-only"` / `"summary"` / `"detail"`） |
| `PythonImport` / `expand_functions`（AgentDefinition 字段） | `tools["ipykernel"]["imports"]` + `expand_functions` | 成为 IpykernelToolBackend 的 ToolConfig 配置 |
| 无 | `MailBox` | 新增：Actor 的消息入口，接收定时触发、跨 Agent 的唤醒请求等 |
| 无 | `Budget` | 新增：steps / tokens 上限，从 AgentDefinition 派生 |
| 无 | `Registry[T]` + `SmartProxy` | 新增：ToolBackend 集合的批量调用辅助；`tool_backends.each.method(cap_dict)` 自动 select+broadcast |
| 无 | `Stage` | 新增：资源容器（mailbox/eventbus/runtime/tool_backends），对应"进程"概念；`Actor` 基类 wrap 它，裸驱动可直接持有 `Stage` |

### 保留不变的部分

- `yuullm.Message` / `Tool` 协议接口
- `RuntimeEvent` 事件名和 payload 结构
- `PythonSession` / `PythonKernelConfig` 的核心逻辑
- `@tool` 装饰器和 `FunctionTool`（作为 ToolExecutor 内部实现细节保留）


### 移除

1. persistency. 现在history显式建模。下游想存自己存一下就行了。

---

## 配置文件

yuuagents 负责解析 AgentDefinition。Actor 的初始化（mailbox、eventbus、tool_backends 等）是下游自己的事，yuuagents 不约束。

```toml
# shiori.toml  ── 由 yuuagents 解析，一个 agent 一个文件

[llm]
provider   = "anthropic"
model      = "claude-sonnet-4-6"
max_tokens = 8096

[budget]
max_steps  = 80
max_tokens = 200_000

[tools.ipykernel]
imports          = [{module = "my_app.agent_tools", alias = "tools"}]
expand_functions = ["tools.*", "-tools.delete_*"]
state            = {tenant_id = "acme"}

[tools.fileop]
[tools.bash]
[tools.schedule]

[prompt]
system = "You are Shiori, a helpful assistant."

[tools.ipykernel.spec]
level = "detail"

[tools.fileop.spec]
level = "summary"

[tools.bash.spec]
level = "type-only"

[tools.schedule.spec]
level = "detail"
```

加载：

```python
# 从文件加载（TOML）
shiori = AgentDefinition.from_file("shiori.toml")

# 从 dict 加载（数据库、HTTP API、动态生成均可）
shiori = AgentDefinition.from_dict(row["config"])

# 直接构造 struct（类型安全，适合代码内组合）
shiori = AgentDefinition(
    llm=LlmConfig(provider="anthropic", model="claude-sonnet-4-6", max_tokens=8096),
    budget=BudgetConfig(max_steps=80, max_tokens=200_000),
    tools={"ipykernel": {...}},
    prompt={"system": "You are Shiori.", "ipykernel": {"level": "detail"}},
)
```

> **配置来源无限制**：`AgentDefinition` 只关心数据是否符合 schema，不关心数据来源。TOML 文件只是一种序列化格式；数据库存储、HTTP 拉取、代码内组合、热加载覆盖均支持，下游自行决定如何获取并转换成合法 struct 传入。

---

## 下游使用

yuuagents 不要求继承 Actor 基类。直接持有 `Stage` + 调用 `create_agent()` 即可驱动。`Actor` 基类是一个推荐实现，不是强制接口。

### 最简驱动

```python
stage = Stage(
    mailbox   = MailBox(...),
    eventbus  = EventBus(),
    runtime   = Runtime(eventbus, config),
    tool_backends = Registry({"ipykernel": IpykernelToolBackend(kernel_config), "bash": BashToolBackend()}),
)

definition = AgentDefinition.from_file("shiori.toml")
agent = create_agent(stage, definition)

agent.append_message(user("帮我查一下订单状态"))

while not agent.done():
    await agent.call_llm()    # LLM 返回的 Message（含 tool_calls）自动追加进 history
    await agent.call_tools()  # 读取 history 尾部的 pending tool_calls，全部执行并追加结果

await agent.close()
```

### 继承 Actor：override run_agent_loop

需要改写 Agent 循环行为时（如 budget 超限后 compact 再续跑），override `run_agent_loop`：

```python
class MyActor(Actor):

    async def run_agent_loop(self, agent: Agent) -> None:
        while not agent.done():
            if agent.budget.steps_exceeded():
                # compact：截断 history，保留 system + 最近 N 条，注入摘要
                summary = await self._summarize(agent.history)
                agent.replace_history([
                    system(agent.definition.prompt.system),
                    user(f"[context summary]\n{summary}"),
                    *agent.history[-10:],
                ])
                agent.budget.reset_steps()

            await agent.call_llm()
            await agent.call_tools()
```

其他可 override 的场景：
- `on_tool_error`：工具报错时决定是 retry、跳过还是终止
- `on_llm_step`：每次 LLM 返回后插入 human-in-the-loop 确认
- `create_agent`：注入额外 executors（如 actor-owned 的 ScheduleExecutor）

### 扩展工具：自定义 ToolBackend

```python
class MyDatabaseProvider:

    def create_executor(self, tool_config: dict) -> "MyDatabaseExecutor":
        return MyDatabaseExecutor(dsn=tool_config["dsn"])

    def create_tool_specs(self, spec_config: dict) -> list[dict]:
        return _render_db_specs(level=spec_config.get("level", "detail"))


class MyDatabaseExecutor:

    def __contains__(self, tool_name: str) -> bool:
        return tool_name in {"db_query", "db_upsert"}

    async def run(self, tool_name: str, payload: dict, sink: UsageSink) -> ContentLike | BackgroundTask:
        sink.declare_free("billed externally")
        match tool_name:
            case "db_query":  return json.dumps(await self._db.fetch(payload["sql"]))
            case "db_upsert": await self._db.execute(payload["sql"]); return "ok"
        raise KeyError(tool_name)

    async def aclose(self) -> None:
        await self._db.close()
```

在 agent 配置中声明：

```toml
[tools.mydb]
dsn = "postgresql://localhost/prod"

[tools.mydb.spec]
level = "detail"
```

### 扩展 Python 函数

直接在配置里声明，无需写任何 Python 扩展代码：

```toml
[tools.ipykernel]
imports = [
    {module = "my_app.data_tools",   alias = "data"},
    {module = "my_app.report_tools", alias = "report"},
]
expand_functions = [
    "data.*",             # 首行 docstring
    "+report.render_pdf", # 完整 docstring
    "-data.delete_*",     # 排除危险函数
]
state = {user_id = "u123"}
```

Agent 在 kernel 启动后直接 `import data` 即可调用；函数签名和 docstring 已注入 `execute_python` 的 description。

### 观测

```python
class CostObserver:
    def on_event(self, event: RuntimeEvent) -> None:
        if event.name == "llm.finished":
            record_cost(event.agent_name, event.data["cost"])

eventbus.subscribe(CostObserver())
eventbus.subscribe(BudgetTracker())  # 内置，消费 runtime.usage_reported 做累加
```

---

## 关键约束备忘

- **ToolExecutor 不负责创建**：`create_agent(stage, definition)` 内部调 `stage.tool_backends.each.create_executor(cap_dict)` 创建；Runtime 只做注册、路由、drain。
- **actor-owned executor** 必须在 Actor.close() 里手动 `aclose()`；`Agent.close()` / `expire_agent` 只解除该 Agent 的路由，不关闭 actor-owned executor。
- **UsageSink 必须 acknowledge**：每个 `ToolExecutor.run()` 调用必须调 `sink.charge()` 或 `sink.declare_free()`，否则 Runtime 在 strict 模式下 raise。
- **BackgroundTask 的 stdin/stdout** 是 `io.StringIO`，外界通过 Background ToolBackend 的工具（check / write / close）与之交互；Agent 侧看到的只是 `task_id` 字符串。
- **ScheduleToolBackend 的 actions** 最多三项（成功分支 / 失败分支）；`agent:<name>:<expr>` 往 Actor.mailbox 投消息，Actor 自己决定是新开 Agent 还是追加到现有 Agent。

---

## 预期文件夹结构

```
src/yuuagents/
├── __init__.py
├── py.typed
│
├── # ── 核心层 ──────────────────────────────────────
├── registry.py        # Registry[T], SmartProxy
├── eventbus.py        # EventBus, RuntimeEvent
├── mailbox.py         # MailBox
├── budget.py          # Budget, UsageSink
├── runtime.py         # Runtime, Task, BackgroundTask
├── stage.py           # Stage
├── definition.py      # AgentDefinition, ToolDefinition, PromptDefinition
├── agent.py           # Agent（仅 call_llm / call_tools / no_more_tools_and_users）
├── actor.py           # Actor, create_agent()
│
├── # ── 保留层（逻辑不变，接口微调）────────────────────
├── tools.py           # @tool, FunctionTool（ToolExecutor 内部实现细节）
├── errors.py          # ToolError
├── python_session.py  # PythonSession, PythonKernelConfig, MimeBundle, PythonExecResult 等
├── python_runtime.py  # PythonImport, expand_functions 过滤逻辑
│
└── tool_backends/
    ├── __init__.py
    ├── base.py        # ToolExecutor Protocol, ToolBackend Protocol（类型定义）
    ├── ipykernel.py   # IpykernelToolBackend, IpykernelExecutor
    ├── bash.py        # BashToolBackend, BashExecutor
    ├── fileop.py      # FileOpToolBackend, FileOpExecutor
    ├── background.py  # BackgroundToolBackend, BackgroundExecutor（actor-owned）
    ├── schedule.py    # ScheduleToolBackend, ScheduleExecutor（actor-owned）
    └── sleep.py       # SleepToolBackend, SleepExecutor
```

**必须删除的旧文件**（一个不留）：

| 文件 | 原因 |
|------|------|
| `engine.py` | 拆为 `Actor` + `Runtime` |
| `billing.py` | 替换为 `UsageSink.charge()` + `budget.py` |
| `persistence.py` | history 显式建模，persistency 移除 |
| `steps.py` | `AgentStep` 等 iterator 语义废弃，替换为 `call_llm/call_tools` |
| `observer.py` | `RuntimeObserver` 列表注册机制替换为 `EventBus.subscribe()` |
| `trace_sink.py` | 以 EventBus subscriber 形式重写后放到 `tool_backends/` 或单独包，不在 yuuagents 核心 |
| `kernel.py` | 内容并入 `python_session.py` 或 `tool_backends/ipykernel.py` |

---

## 验收条件

### 1. 文件无残留

- [ ] `src/yuuagents/` 中不存在任何"必须删除"列表中的文件
- [ ] `grep -rn "class Engine" src/` 为空
- [ ] `grep -rn "AgentStep\|LlmStep\|ToolStep\|BackgroundStep\|ErrorStep" src/` 为空
- [ ] `grep -rn "BillingSink\|BillingRecord\|InMemoryBillingSink" src/` 为空
- [ ] `grep -rn "AgentSnapshot\|MemoryAgentStore\|persistence" src/` 为空
- [ ] `grep -rn "BackgroundTaskHandle" src/` 为空（旧 handle，非新 BackgroundTask）

### 2. 公开 API 正确

`from yuuagents import X` 可以导入，X 涵盖且仅涵盖：

**核心**：`Stage`, `Runtime`, `Actor`, `Agent`, `create_agent`,
`Budget`, `UsageSink`,
`EventBus`, `RuntimeEvent`, `MailBox`,
`Registry`,
`AgentDefinition`, `ToolDefinition`, `PromptDefinition`,
`Task`, `BackgroundTask`

**工具辅助**：`tool`, `FunctionTool`, `ToolError`

**Python 能力**：`PythonSession`, `PythonKernelConfig`, `PythonImport`,
`PythonExecResult`, `MimeBundle`（等 python_session 公开类型）

**ToolBackend 类**：从 `yuuagents.tool_backends` 可导入
`IpykernelToolBackend`, `BashToolBackend`, `FileOpToolBackend`,
`BackgroundToolBackend`, `ScheduleToolBackend`, `SleepToolBackend`

以下名字不得出现在 `__init__.py` 的 `__all__` 中：
`Engine`, `AgentStep`, `LlmStep`, `ToolStep`, `BackgroundStep`, `ErrorStep`,
`BillingRecord`, `BillingSink`, `InMemoryBillingSink`,
`AgentSnapshot`, `MemoryAgentStore`,
`InMemoryObserver`, `RuntimeObserver`, `YuuTraceObserver`,
`BackgroundTaskHandle`

### 3. 语义正确

- [ ] `Agent` 无 `steps()` 方法；只有 `call_llm()`, `call_tools()`, `done()`, `history`, `append_message()`
- [ ] `Runtime.submit()` 返回 `Task`；`Task.wait()` 超时时不 cancel 原 task，自动调 `submit_bg()`
- [ ] `ToolExecutor.run()` 未调 `sink.charge()` 或 `sink.declare_free()` 时，strict mode 下 `Runtime` raise（而非仅 warn）
- [ ] `AgentDefinition.from_file("*.toml")` 可解析 goal.md 配置示例中的 `shiori.toml` 格式
- [ ] `AgentDefinition.from_dict(d)` 接受等价 dict，结果与 `from_file` 一致
- [ ] `AgentDefinition(...)` 直接构造合法 struct 不报错；三种入口最终得到同一内部表示
- [ ] `Registry.each.<method>(cap_dict)` 只广播 cap_dict 键与 registry 键的交集

### 4. 测试

- [ ] `tests/test_rfc2_runtime.py` 删除或完全重写为新 API（不得有任何对旧 `Engine` / `AgentStep` 的引用）
- [ ] 新测试文件至少覆盖：
  - `Registry` / `SmartProxy` 交集广播逻辑
  - `Runtime.submit()` → `Task.wait()` 正常路径
  - `Task.wait()` 超时自动移入后台路径
  - `UsageSink` strict mode raise
  - `Agent` `call_llm` → `call_tools` 完整循环（可 mock LLM）
  - `AgentDefinition.from_file()` 解析正确

### 5. 类型与质量

- [ ] `mypy --strict src/yuuagents/` 无错误（或已知豁免已在 pyproject.toml 中注明）
- [ ] `ruff check src/` 无错误
