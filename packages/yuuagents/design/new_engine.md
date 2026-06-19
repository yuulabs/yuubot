AgentDefinition =  ToolDefinition + PromptDefinition (两者统称Definition)
AgentDefinition用于生产一个Agent.

Agent = Context(History + Tool Spec) + Runtime + EventBus（用于向Observers发布可观测事件） + Budget（控制steps, tokens）

Agent主要的作用是“推动一步”。考虑到开发的简便（曾经，我们使用过 `steps` api，这虽然保证调用总是合法的，但是让返回值处理变得很麻烦（llm & tool混在一起），这一次Agent直接暴露准确的 `llm`, `tool call`语义。

> agent主要是封装复杂的消息拼接和内部观测事件等。Actor只需关心history被推进了即可。

## Definition

Agent的Definition分为两部分。

1. ToolDefinition. 这一部分代表了Agent的能力需求，属于 **资源分配**。Actor会阅读这一部分内容，不同Actor行为不同（某些可能会进行资源复用）。它最终会产出一组tool! （系统可见）
2. PromptDefinition. 这一部分代表了Agent的prompt结构定义。Actor只是简单执行prompt构造。它最终产出system prompt & tool specs. 值得注意的是，这里故意发生了解耦（因为tool的spec对不同的agent可能变化。PromptDefinition唯一确定了Agent可见的文本。

在这种切分下，资源所有权有一个明确约束：

1. ToolBackend 的 `create_executor()` 每次调用都表示申请一份全新资源；ToolBackend 不做 executor 复用。
2. 如果 Actor 希望跨 Agent 复用资源，应由 Actor 自己预先创建并持有 executor，再以 actor-owned 的方式把同一个 executor 对象路由给多个 Agent。
3. 因此“复用”发生在 Actor 层，而不是 ToolBackend 层；ToolBackend 只负责根据 ToolConfig 创建一份新资源，或为 prompt 生成 tool specs。

并且PromptDefinition单独列出，对于可观测性非常友好（直接展示sections而不是展示最终渲染的system prompt（通常非常长））。

## LLM ToolBackend

`Stage` 持有一个独立的 `llm_client` 字段，与工具 ToolBackend 分开配置。LLM 调用参数按 `yuullm.YLLMClient.stream()` 的 kwargs 透传（如 `model`、`max_tokens`、`temperature` 等），yuuagents 不解释这些字段的业务含义。`Agent.call_llm()` 直接持有 llm client 引用，不经过 Registry 路由。

```py
@define
class Stage:
    mailbox:      MailBox
    eventbus:     EventBus
    runtime:      Runtime
    tool_backends:    Registry[ToolBackend]
    llm_client: LlmClient          # yuullm client，Agent.call_llm() 使用
    llm_options:  dict               # 默认透传给 yuullm.stream() 的 kwargs
```

---

## ToolBackend 

ToolBackend: 资源分配和”prompt来源”的最小抽象。在Actor创建时即确定。（Actor需要配置ToolBackend）

Actor负责根据ToolDefinition + Self State决定怎么调用ToolBackend创建ToolExecutor & 组装丢给Agent。

Actor还会将PromptDefinition丢给ToolBackend让它产出匹配的tool spec（但是不创建资源）。

> ToolDefinition 是 per-provider的 dict. 每一项由provider进行解释，产出tool和资源（因为资源可能跨tool使用，例如同一组tool都是在操纵同一个后台数据库，粒度是provider而非tool）。

> PromptDefinition也是per-provider dict. 但有一个约定字段level: {"level":"type-only"/"summary"/"detail", ...(provider-specific)}. 如果Actor希望自己注入system prompt来解释一些概念（避免provider之间重复解释），就可以通过type-only或者summary来削减tool spec详细程度。

### ToolExecutor

ToolExecutor是”持有资源”的最小抽象。它代表了一组Tool. 由Actor按需创建和复用。

ToolExecutor可产出一个Task. 这个Task将会被等待特定时间，如果超时则移入后台被Kernel接管，Kernel将在完成时往mailbox丢消息。

ToolExecutor分两种所有权：

- **agent-owned**：随 `create_agent` 创建，生命周期与 agent 一致，agent expire 时由 Runtime 负责关闭。
- **actor-owned**（如 ScheduleExecutor）：Actor 预先创建并长期持有，可被多个 agent 复用，Runtime 只建立路由关联，不负责关闭；Actor 在自身关闭时统一清理。

### Runtime

Runtime是管理Executors的地方。具体来说

1. ToolExecutor的添加、退出 & 检索（注意不负责创建）
2. 长时间任务的托管单位

Runtime 只记录 executor 对象及其对 Agent 的路由关系。实现可以使用 handle/container 简化心智模型，关键语义是：

```py
agent2executors: dict[AgentId, list[ExecutorHandle]]

@define
class ExecutorHandle:
    name: str
    executor: ToolExecutor
    owned: bool      # True: agent-owned；False: actor-owned
```

`remove_agent` 时，只对该 Agent 路由里的 `owned=True` executor 执行 drain & close；actor-owned 的只解除路由关联，不关闭。由于 ToolBackend-created executor 明确不复用，`owned=True` 的关闭不需要处理跨 Agent 引用；跨 Agent 共享必须建模成 actor-owned。

### Helper: Registry

```py
from typing import TypeVar, Generic, Dict, Iterable, Any, cast, Callable, Self

T = TypeVar("T")

class SmartProxy(Generic[T]):
    def __init__(self, target_dict: Dict[str, T]):
        self._items = target_dict

    def __getattr__(self, name: str):
        async def wrapper(*args, **kwargs):
            # dict 参数同时充当 selector + payload：
            #   有 dict 时，只迭代所有 dict 参数 key 的交集（缺失的 provider 跳过）
            #   无 dict 时，全量广播
            dict_args = [a for a in args if isinstance(a, dict)]
            if dict_args:
                active_keys = set(self._items) & set.intersection(*(set(d) for d in dict_args))
            else:
                active_keys = set(self._items)

            results: Dict[str, Any] = {}
            for key in active_keys:
                obj = self._items[key]
                unpacked_args = [a[key] if isinstance(a, dict) else a for a in args]
                unpacked_kwargs = {k: v[key] if isinstance(v, dict) else v for k, v in kwargs.items()}
                results[key] = await getattr(obj, name)(*unpacked_args, **unpacked_kwargs)

            return Registry(results)
        return wrapper

class Registry(dict[str, T], Generic[T]):
    def select_intersect(self, keys: Iterable[str]):
        return Registry({k: self[k] for k in keys if k in self})  ## keys可以不在
    def select(self, keys: Iterable[str]) -> 'Registry[T]':
        """筛选子集，返回的依然是 Registry，支持链式调用"""
        return Registry({k: self[k] for k in keys})  ## keys必须全位于keys中。不在的话dict[]会自动报错

    @property
    def each(self) -> T:
        """触发异步智能代理（ToolBackend 方法默认为 async）"""
        return cast(T, SmartProxy(self))

    @property
    def sync_each(self) -> T:
        """同步版智能代理，用于适配同步接口（如 create_tool_specs）"""
        # SmartProxy 的同步变体，wrapper 不 await；需要类似 SmartProxy 的实现
        return cast(T, SyncSmartProxy(self))
```

## MailBox

```py
@define
class MailMessage:
    mid:     UUID
    content: Content | None    # yuullm.Content，可直接追加进 Agent history

# 内置子类
@define
class ScheduleTriggerMessage(MailMessage):
    agent_name: str = ""
    job_id:     str = ""

@define
class BackgroundCompletedMessage(MailMessage):
    task_id:  str = ""
    agent_id: str = ""
```

MailBox 是 Actor 的消息入口。Schedule / Background 等 ToolBackend 在触发时往 MailBox 投具体的 `MailMessage` 子类；Actor 在 `run()` 里循环 `await mailbox.recv()`，用 `match`/`isinstance` 按类型路由到对应 Agent 或新建 Agent。下游可继承 `MailMessage` 添加自定义消息类型。

---

## Stage：资源容器

`Stage` 是持有所有运行时资源的"进程"单元，与行为无关。

```py
@define
class Stage:
    mailbox:      MailBox
    eventbus:     EventBus
    runtime:      Runtime
    tool_backends:    Registry[ToolBackend]
    llm_client: LlmClient
    llm_options:  dict

    @classmethod
    def from_config(cls, config) -> 'Stage':
        mailbox      = MailBox(config.mailbox)
        eventbus     = EventBus(config.eventbus)
        runtime      = Runtime(mailbox, eventbus, config.runtime)
        # create_providers 遇到 'background'/'schedule' 时放空占位（二者依赖 runtime/mailbox，信息不足）
        tool_backends    = create_providers(config.tool_backends)
        # background 必须在 runtime 之后补入，因为后台任务托管本身依赖 runtime
        tool_backends['background'] = create_bg_provider(runtime, config.tool_backends.get('background'))
        # schedule 需要 mailbox 作为触发结果的投递目标，同理
        tool_backends['schedule']   = create_schedule_provider(mailbox, config.tool_backends.get('schedule'))
        llm_client, llm_options = create_llm_client_and_stream_options(config.llm)
        return cls(mailbox, eventbus, runtime, tool_backends, llm_client, llm_options)
```

## Actor创建Agent逻辑

`create_agent` 是独立函数，接受 `Stage` 和 `AgentDefinition`，可在不继承 Actor 时直接调用。`Actor` 基类只是把 Stage 和这套函数打包成更方便 override 的形式。

```py
def create_agent(stage: Stage, definition: AgentDefinition) -> Agent:
    agent_id  = uuid4()
    executors = stage.tool_backends.each.create_executor(definition.tools)  # dict 同时充当 selector
    stage.runtime.add_executors(agent_id, executors, owned=True)
    specs     = stage.tool_backends.each.create_tool_specs(definition.tools)
    llm_options = {**stage.llm_options, **definition.llm.stream_kwargs()}
    return Agent(agent_id, specs, [Message(system, definition.prompt.system)],
                 stage.runtime, stage.eventbus, stage.llm_client, llm_options, Budget(definition))


class Actor:
    def __init__(self, stage: Stage, actor_executors: Mapping[str, ToolExecutor] | None = None):
        self.stage = stage
        self.actor_executors = dict(actor_executors or {})

    def create_agent(self, definition) -> Agent:
        return create_agent(self.stage, definition, actor_executors=self.actor_executors)

    async def expire_agent(self, agent):
        await self.stage.runtime.remove_agent(agent.agent_id)
```

### Actor 创建持有定时任务的Agent示例

```py
def __init__(self, ...):
    ...
    self.schedule_executor = ScheduleExecutor(mailbox=self.stage.mailbox, db_path=db_path)
    self.actor_executors["schedule"] = self.schedule_executor
    # 不注册到 runtime；create_agent 时只给声明了 schedule 的 agent 补路由

def create_schedule_agent(self, definition):
    return create_agent(self.stage, definition, actor_executors=self.actor_executors)

async def close(self):
    await self.stage.runtime.close_all()
    await self.schedule_executor.aclose()  # Actor 自己在关闭时清理 actor-owned executor
```

## Runtime执行

```py
class Runtime:
    def submit(self, agent_id: str, tool_call: ToolCall, budget: Budget, timeout: float = 300.0) -> 'Task':
        self.eventbus.emit("runtime.task_created", {"agent_id": agent_id, "tool_call": tool_call, "timestamp": monotonic()})
        # 伪码：遍历 agent2executors[agent_id] 对应的 executor_pool 条目，
        # 找到第一个 tool_call.name in executor 的 executor，调用其 run()
        # 若无匹配，next() 抛 StopIteration，在 async 上下文被包装为 RuntimeError，
        # 由 wait_with_error_handling() 捕获后以错误字符串回传 LLM——这是预期行为。
        executor = next(self.executor_pool[eid] for eid in self.agent2executors[agent_id]
                        if tool_call.name in self.executor_pool[eid])
        task_id = uuid4()
        sink = UsageSink(self.eventbus, task_id, budget)
        raw = asyncio.create_task(executor.run(tool_call.name, tool_call.payload, sink))
        task = Task(task_id=task_id, agent_id=agent_id, ddl=monotonic() + timeout - 5.0, task=raw, runtime=self, sink=sink)
        self.tasks[task.task_id] = task
        return task
    def submit_bg(self, task_id, metadata, bg_task):
        self.eventbus.emit("runtime.task_move_to_bg")
        #注册add_done_callback(), 即往self.mailbox投递完成信息和结果 & 发送runtime.task_completed事件
        #如果被cancel之类的，还需要发相关事件。总之，runtime追踪task的生存周期，而tool call的开启 & 结束则由agent来负责。
@define
class Task:
    task_id: UUID
    agent_id: str
    ddl: float          # monotonic 时间戳
    task: asyncio.Task
    runtime: Runtime
    sink: UsageSink

    async def wait(self) -> ContentLike | str:
        duration = self.ddl - monotonic()
        if duration > 0:
            # asyncio.wait 返回 (done, pending)，超时后不 cancel，task 继续跑
            done, pending = await asyncio.wait({self.task}, timeout=duration)
        else:
            done, pending = (({self.task},  set()) if self.task.done() else (set(), {self.task}))

        if done:
            result = self.task.result()
            if isinstance(result, ContentLike):
                self.runtime.eventbus.emit("runtime.task_completed", {"task_id": self.task_id})
                self.runtime._check_sink(self.sink)
                return result
            elif isinstance(result, BackgroundTask):
                self.runtime.submit_bg(self.task_id, {}, result)
        else:
            # 超时，task 仍在跑，移入后台
            bg = BackgroundTask(stdin=io.StringIO(), stdout=io.StringIO(), task=self.task)
            self.runtime.submit_bg(self.task_id, {"agent_id": self.agent_id}, bg)

        return f"已移至后台，task_id={self.task_id}，完成后自动通知"

    async def wait_with_error_handling(self) -> ContentLike | str:
        """Runtime 调用此入口；task 抛出异常时转为带上下文的字符串回传 LLM，
        与 bg task 返回 / 正常工具返回走同一条路径，只是 content 不同。"""
        try:
            return await self.wait()
        except Exception as exc:
            self.runtime.eventbus.emit("runtime.task_error", {"task_id": self.task_id, "error": repr(exc)})
            return f"工具调用出错（task_id={self.task_id}）：{type(exc).__name__}: {exc}"
```

### ToolExecutor规范

```py
class ToolExecutor(Protocol):
    async def run(self, tool_name: str, payload: dict, sink: UsageSink) -> ContentLike | BackgroundTask:
        ...
    def __contains__(self, tool_name: str) -> bool:
        ...
    async def aclose(self) -> None:
        ...
```

Runtime 在 `remove_agent` 时，对 agent-owned executor 调用 `_drain_and_close`：等待所有归属该 executor 的 background task 完成（或超时强制 cancel），再调 `aclose()`。不依赖 `__del__`。

#### UsageSink 与计费

Runtime 在 `submit()` 里创建 sink 并传入 budget，task 完成后调 `_check_sink()` 验证 acknowledged（strict mode raise）。

```py
@define
class UsageSink:
    _eventbus: EventBus
    _task_id:  UUID
    _budget:   Budget
    _acknowledged: bool = False

    def charge(self, service: str, amount: float, unit: str):
        self._acknowledged = True
        self._budget.charge(unit, amount)                        # 同步更新 Agent 的 Budget
        self._eventbus.emit("runtime.usage_reported", {
            "task_id": self._task_id, "service": service, "amount": amount, "unit": unit,
        })                                                       # 外部账单/监控用

    def declare_free(self, reason: str):
        """显式豁免计费，必须写明理由。"""
        self._acknowledged = True

    def __del__(self):
        if not self._acknowledged:
            warnings.warn(f"UsageSink for task {self._task_id} was never acknowledged", stacklevel=2)
```

`Agent.call_tools()` 调用 `runtime.submit()` 时显式传入自身 budget，因果关系在调用方一眼可见：

```py
task = self.runtime.submit(self.agent_id, tool_call, self.budget, timeout=300.0)
```

Runtime 不维护 `agent_id → budget` 映射表；budget 归属完全由 Agent 自己管理。

#### Budget

```py
@define
class Budget:
    limits: dict[str, float]        # {"steps": 80, "tokens": 200_000, "usd": 5.0}
    _usage: dict[str, float] = field(factory=dict)

    def charge(self, unit: str, amount: float) -> None:
        self._usage[unit] = self._usage.get(unit, 0.0) + amount

    def is_exceeded(self) -> bool:
        return any(self._usage.get(u, 0.0) >= limit for u, limit in self.limits.items())

    def reset_steps(self) -> None:
        self._usage.pop("steps", None)
```

Budget 是纯粹的累加器，不关心 unit 的业务含义。定价逻辑（token × price）由各 ToolExecutor 自己处理后 charge `"usd"`；折扣/免费额度也由 ToolExecutor 决定 charge 多少。`runtime.usage_reported` 事件只供外部账单系统/监控订阅，不驱动 Budget。

```py
@define
class BackgroundTask:
    stdin: io.StringIO
    stdout: io.StringIO
    task: asyncio.Task
```

外界可以往stdin写或者从stdout读完成交互。

### Tool Spec

Tool Spec就是标准的Openai Tool Schema. 这是单纯的说明性数据（实际上，ToolExecutor可以随便怎么解释name & payload，从类型的角度来说）。

## 关键对象与职责

class EventBus:
    - subscribe(observer)
    - emit(event_name, payload)

class Stage:
    - from_config(config) -> Stage
    - 持有 mailbox / eventbus / runtime / tool_backends

class AgentDefinition:
    - tools配置
    - prompt配置
    - 校验规则

class Runtime:
    - add_executors(agent_id, executors: dict[str, ToolExecutor], owned: bool)
    - remove_agent(agent_id)  # async；drain & aclose agent-owned，解除 actor-owned 路由
    - close_all()  # async；关闭所有 runtime-owned executor，解除 actor-owned 路由
    - submit(agent_id, tool_call, timeout) -> Task
    - submit_bg(task_id, metadata, bg_task)
    - manage_long_running_tasks()

class Agent:
    - init(definition, messages, runtime, event_bus)
    - append_messages(messages)
    - call_llm() -> Message                  # 调 LLM，将返回的 Message（含 tool_calls）追加进 history；从 stream result 中提取 token/cost，直接 charge 到 self.budget（LLM 开销由 Agent 自己累计，不经过 UsageSink）；同时 charge 1 step
    - call_tools()                           # 读取 history 尾部待执行的 tool_calls，逐一 submit & await（runtime 不阻塞，本质并发），将结果追加进 history；无 pending tool_calls 时为空操作
    - done()                                 # history 尾部无 pending tool_calls 且无待处理 user 消息时返回 True；append_message() 后可能重新变 False
    - history

class Actor:
    - init(definition, mailbox, event_bus)
    - run()
    - run_agent_loop(agent)

class Observer:
    - on_event(event)
 

## 内置ToolBackend & 工具


若无注明，所有provider的标记名均为去掉provider的全小写。例如FileOpToolBackend->"fileop"（配置文件中）

### 管理工具

#### Background ToolBackend

1. 检查background（获取stdout尾部）
2. 关闭background
3. 向background写入

与Actor绑定。

#### Schedule ToolBackend

1. 创建cron job。(cron, actions, once). action是一个字符串{type:expr}. action有两种可能 1. 执行一个bash命令。如 "bash:ls -l". 2. 向Actor的mailbox写入一条唤醒特定agent的请求消息。此时表达式为 {agent:<name>:expr}. expr作为first user msg. 如 agent:shiori:say hello to qq group memebers! once表达是否只执行一次。actions是一个list. 只有一个：执行该action. 有两个：如果第一个action成功（bash返回值为0），则第二个。 有三个：如果第一个成功，则第二个；否则第三个。因此可以写出["bash:bash check_status.sh", "agent:main:任务已完成！", "agent:main:任务失败了……需要检查xxxx"]。

Actor自己负责解决并发agent问题（可能涉及到resources冲突）。一般有两种可能，取决于实现 1. 开两个隔离的agent 2. 把消息扔到现在活跃的agent后面（可能打断当前处理流程）。

2. 查看 cron job （包括内容和近期触发记录）
3. 删除 cron job 

该provider对应的executor存活期很长，通常和actor保持一致而非特定agent。


#### SleepToolBackend

1. Sleep. 一个更加轻量的控制器。单纯阻塞指定时间后返回当前最新时间。常用于bot需要等待一个可能会出错的长程序（因为不知道到底是卡了还是在跑，所以必须agent自己来看）。Sleep至多30min，以避免卡死整个协程。

### 实用工具

#### FileOpToolBackend

1. read_file. 支持阅读多模态文件（直接读出Content避免序列化问题）。
2. edit_file. 精准字符串替换

#### BashToolBackend

1. 一次性bash任务。其环境变量继承自主进程（一切行为基本与主进程一致，没有surprise行为）。支持background。

#### IpykernelToolBackend -- 超级工具&下游主要扩展点

1. 提供 `execute_python` 工具。超级万能工具。允许agent像写真的jupyter notebook一样执行任意代码。具体来说，该工具连接一个Ipykernel核，就像正常运行notebook一样操作。工具存在硬超时，若超时则interrupt执行。agent可以使用python内置的async编写长任务。
2. 提供扩展点，允许外部扩展模块和函数。

细节：

1. 不支持自动background（因为后台kernel已经卡死）。agent应该自己避免写长阻塞任务。善用asyncio Task管理。
2. session通常和agent生命周期一致。每个llm step之间变量共享，因此可以指示Agent在代码中复用变量避免反复搬运context。

##### 内置modules & functions

IpykernelToolBackend不把每个函数注册成独立tool，只暴露一个 `execute_python`。扩展能力通过“可导入模块 + 函数文档展开”完成：

```py
PythonRuntime(
    config=PythonKernelConfig(sys_path=("/app/src",)),
    imports=(PythonImport("my_app.agent_tools", alias="tools"),),
    state=JsonSessionState({"tenant_id": "acme"}),
    expand_functions=("tools.search_orders", "+tools.update_ticket"),
)
```

Kernel bootstrap时：

1. 将provider源码路径和 `config.sys_path` 加入 `sys.path`。
2. import `PythonImport.module`；若有 `alias`，写入 `sys.modules[alias]`，agent可直接 `import tools`。
3. 注入 `SESSION_STATE` / `get_session_state()` / `TASKS`。
4. 执行 `startup_code`。

##### expand_functions过滤原理

`expand_functions` 只控制哪些函数签名和docstring出现在 `execute_python` 的description里，不是安全边界。Agent可以这么调用：

```py
import tools
await tools.search_orders(...)
```

函数候选集构造规则：

1. 有 `__all__`：只看 `__all__` 里指向function的名字。
2. 无 `__all__`：看模块中不以 `_` 开头的function。
3. 类、常量、客户端对象不会展开。

pattern规则：

1. `pattern`: include，展示签名 + docstring首行。
2. `+pattern`: include，展示完整docstring。
3. `-pattern`: exclude，移除已选择函数。
4. 顺序执行，后面的规则覆盖前面的规则。（因此请将 * 写在最前面）
5. 支持glob：`tools.*`、`tools.search_*`、`search_orders`。
6. 匹配名包括：`name`、`module.name`、`alias.name`。  

默认规则：

1. `None`: 每个import模块默认展开前24个公开函数，docstring只取首行。
2. `()`: 不展开函数。
3. `("tools.*", "-tools.delete_*")`: 先选一批，再删除危险项。
4. `("+tools.update_ticket",)`: 展示完整函数说明。

`import_modules` 和 `expand_functions` 也可以放在 `AgentDefinition` 上，作为per-agent能力面。不同agent可以把不同模块都alias成 `tools`，互不污染。推荐扩展包用 `__all__` 声明公开API，`expand_functions` 只负责控制prompt展开粒度。
