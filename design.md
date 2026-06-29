# Design: yuubot Core Backend Concepts

## Problem / Goal

当前的yuubot/yuuagents/yuullm拆分简直是一坨屎。

这篇文章描述了最简单的应然模型。

## Concept Map

`Runtime` 管理系统级资源：异步任务、数据库、缓存、Integration 实例、mailbox、
eventbus。

`RuntimeContext` 是一次会话共享的只读上下文树。它把 model、conversation、actor、
workspace、otel、rpc、integrations 等信息交给当前会话下的所有单元。

`Actor` 是可被路由和管理的 agent 实体。Actor 持有 mailbox，并按配置创建隔离的
Conversation。

`Conversation` 是业务会话。它组合 LLM client、History helper、Harness，并自行处理
持久化与 cost。

`Harness` 负责处理 Tool Call：找到 Tool、校验 payload、反序列化、并发执行、返回
ToolResult。

`Tool` 是模型直接调用的能力，例如 `read`、`edit`、`write`、`bash`、
`execute_python`。

`Integration` 是平台/服务连接。它主要规定配置、session context、prompt，以及必要时
的 daemon side route / RPC handler。

`execute_python` 是 agent 的 Python 扩展面。它运行本地 Python 代码，导入 `yb` 和
Integration facade；当本地能力不够时，可以通过手写 RPC 访问 daemon。

## Runtime Context

初始化会话时，yuubot 维护一套完整的上下文树。该 context 被当前会话下所有单元访问。

```text
RuntimeContext = {
  model: ModelCard(selector, capabilities = {vision, toolcall, json}),
  conversation_id: str,
  integrations: dict[name, IntegrationContext],
  actor: str,
  otel: dict,
  workspace: path,
  rpc: dict,
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `model` | 当前会话使用的模型选择器与能力信息。 |
| `conversation_id` | 当前业务会话 id。 |
| `integrations` | Integration 产出的跨进程 context。 |
| `actor` | 当前 actor id。 |
| `otel` | OpenTelemetry 上下文传播所需数据。 |
| `workspace` | 当前 actor / conversation 可用的 workspace path。 |
| `rpc` | RPC 必要上下文，例如 auth token。 |

`RuntimeContext` 是共享读模型，不是服务定位器。需要什么字段就显式挂上去；先让
builtin tools 和 Integration facade 跑通，再考虑更复杂的依赖注入。

## Runtime / Task / Mailbox

Runtime 管理所有系统资源。

```text
Runtime = {
  tasks: dict[task_id, Task],
  db: Database,
  cache: CachePool,
  integrations: dict[name, Integration],
  mailboxes: dict[address, MailBox],
  eventbus: EventBus,
}
```

`tasks` 是所有异步 Task。可借由 tasks 进行任务管理：查询、增加、中断、删除。

`cache` 是所有需要缓存的部分使用的 cache pool。这是一个 LRU untyped cache，存储格式
为 `{key, meta, data}`，按 `data` 计算容量上限。使用方法通常是调用方用 key 读取；
若未命中则抛出 `KeyError`，然后调用方对比 `meta` 决定是否使用 `data`。该 pool 用于
防止组件隐式缓存导致内存泄漏。

`integrations` 是所有启用的 Integration 实例。

`mailboxes` 是 `{addr: MailBox}`。Actor 创建过程中会往这里注册 mailbox。

`eventbus` 是事件出口，事件全部走这里发射，由外部观测者处理。

```text
Task = (id, coro_factory, stdin, stdout, status, error, result)
coro = coro_factory(stdin, stdout)
```

Runtime 自行运行任务，并正确设置 `error` / `result`。`stdin` / `stdout` 均为字符流，
且使用观察者模式进行观测。

## LLM History & Stream Helper

这里严格定义 LLM History 的格式以及流式输出聚合方案。

```text
History = [ToolSpec = OAIJsonSchema] + system_prompt + [Message]
```

`Message` 分为 `InputMessage` / `GenMessage` / `ToolResult`。

```text
InputMessage = (role, name, list[ContentItem])
```

目前 `InputMessage` 有 `user` / `developer` 两个 role。

`GenMessage` 包含 `GenToolCall` / `GenText` / `GenReasoning` / `GenImage` / `GenAudio`。
每个消息在需要引用 content 时均指：

```text
Content = list[ContentItem]
```

`ContentItem` 包含 `Text`、`Image`、`Audio`、`File`。

```text
ToolResult = (role = "tool", tool_call_id, content)
```

以上定义可通过 adapter 转换至各供应商 API。

将流式 LLM 输出规范化的基石组件：

```py
chunks = [chunk async for chunk in llm.stream()]
outputs = merge(chunks, drop_partial_toolcall=True) -> list[GenReasoning | GenText | GenToolCall]
tool_calls = extract_tool_calls(outputs)
# results = harness.gather(tool_calls)
```

### Stream Protocol

LLM stream 产出：

```text
Gen***Start
Gen***Delta
Gen***End  # 除 ToolCall 以外
GenToolCallName
GenToolCallArgumentsDelta
GenToolCallArgumentsEnd
StreamStop(reason, usage, account)
```

`GenToolCallName` 需要 adapter 完整等到名称，或从过量 stream events 中提取名称。

`reason` 透传 OpenAI `finish_reason`：`stop`、`length`、`tool_calls`、
`content_filter`、`function_call`；另加 yuubot 自己的 `interrupted`。

`usage` 包含 `input_tokens`、`cached_input_tokens`、`output_tokens`、
`PAYG_cost`。`account` 包含账户剩余情况：`credits`（美元计价）、`quota`（百分比额度）
以及 provider-specific 细节，无法统一。`account` 可以为空。`usage.PAYG_cost` 也可以
为空（无法计算）或 `0`（订阅类）。这些细节由各个 provider adapter 自行填入。

yuubot 自行维护一个统一的 pricing estimator，根据 tokens 计算价格，仅用于预估。预估
价格优先级低于 `usage` / `account`。

该协议相比原始 events 可能超发一部分，例如多余的 start / end；这可以容忍，且理论上
总是可以转换，延迟一步即可确定是否 start / end。

`merge` 操作符将 `[start, end]` 中的事件聚合成一个完整 Message。`merge` 会忽略最后的
`StreamStop`。

该协议前后端通用，一体化。给定 `[start, end]`，`merge` 的结果与不使用 stream 的结果
完全一致。因此，前端无需刷新读取后端亦可相信数据。

打断流程：

```text
调用方：

stop_event 在另一个异步循环中可能被设置。

async for chunk in llm.stream(args, stop_event):
  ... 正常消费

llm client:

yield from provider(e.g. oai) client
if stop_event.set():
  若可能，查询一次 provider 的账单接口，制作 StreamStop 块。reason 为 interrupted.
```

## Harness & Builtin Tools

处理 Tool Call 的框架称为 Harness。

```py
class Tool:
  payload_type: Type[msgspec.Struct]

  @classmethod
  def from_config(cls, config, runtime):
    pass

  async def execute(self, payload: Struct) -> str | Content:
    pass

  async def close(self):
    pass
```

`payload` 已反序列化。Tool 可以返回 `Content` 以处理多模态，例如 read image。Tool 允许
多并发，每次 `execute` 调用独立；若存在共享资源，需要自行处理并发竞争。`close` 清理
Tool 自己持有的资源，例如子进程、session、临时文件。

```py
HarnessConfig = {"tools": ..., "workspace": ...}

class Harness:
  @classmethod
  def from_config(cls, config, runtime):
    pass

  async def gather(self, tool_calls, timeout=240) -> list[ToolResult]:
    pass

  async def close(self):
    for tool in self.tools.values():
      await tool.close()
```

Harness 初始化 Tools 并注册。`gather` 找到 Tool、validate 输入、反序列化，并发执行
tool calls，并施加 240s 硬超时。Harness 拥有 Tool 生命周期，关闭时依次关闭所有 Tool。

### Builtin Tools

`read` / `edit` / `write` / `bash` / `execute_python` 是普通 Tool：有 tool spec，也有
runtime-only config。

文件和 shell 工具主要查询 workspace context。`execute_python` 多消费一个跨进程 facade
context：`sys_path`、startup code、actor / session / mailbox identity、Integration
context。

### execute_python

`execute_python` 是 yuubot 的核心工具。Agent 可通过它执行代码，体验类似 Jupyter：
上下文驻留，原生 `await`。

> 实现用 `IPython.core.interactiveshell` 即可，因为不需要 UI。回收时，使用
> `shell.reset()` 或清空全局变量字典来回收用户变量，但是保持运行库 import，因为 import
> 通常非常慢。

`execute_python` 通过手写 SDK 扩展，等价于跑一个 Python 本地库。没有魔法：yuubot 会在
workspace 编译并安装一份 Python 代码过去；如果这个 workspace 是新的，或没有其他 actor
共享，LLM 可以随时在自己的 workspace 安装新依赖。使用 uv 实现依赖共享，避免占用过多磁盘
空间。

示例：

```py
results = await asyncio.gather([yb.web.read(a.html), yb.web.read(b.html)])
# grep results to find what you are interested in
```

`yb.web.read` 仅仅是 `src/yb/web.py` 里的一个函数，运行时被复制到 workspace 下并加入
`sys.path` 以便 import 找到。没有黑魔法。

`execute_python` 本身仅仅是一个tool. 它通过内部的手写facade“扩展”。

```text
execute_python runtime
  imports yb / integrations.*
  code calls integrations.github.main.list_issues(...)
  integration code runs locally in python session
  optional: hand-written RPC to daemon when local is not enough
```

所以 Integration 的普通能力就是本地 Python 代码。daemon 只负责把实时配置和跨进程
context 拼好；需要 daemon 参与的能力，由该 Integration 自己定义一小段手写 RPC。

## Actor / Conversation

`Conversation` 是业务概念。它使用 LLM client、History helper、Harness，并自行处理
持久化 / cost。

```text
Conversation = {
  id: str,
  context: RuntimeContext,
  history: HistoryHelper,
  llm: LLMClient,
  harness: Harness,
  stop_event: Event,
}
```

Conversation 是业务编排层：它不实现 Tool，也不理解 Integration 内部能力；
它只把 History、LLM step、Harness、持久化、可观测性、打断语义串起来。

```py
class Conversation:
  async def run_loop(self, input):
    self.stop_event.clear()
    self.history.append(input)
    self.persist(input)
    self.emit("conversation.input", input)

    while not self.stop_event.is_set():
      with span("llm.step"):
        chunks = [
          chunk
          async for chunk in self.llm.stream(self.history.to_llm_input(), self.stop_event)
        ]
        outputs, stop = merge(chunks)

      self.history.append(outputs)
      self.persist(outputs)
      self.record_cost(stop.usage)
      self.emit("conversation.output", outputs, stop)

      if stop.reason in {"stop", "interrupted"}:
        return outputs

      if stop.reason not in {"tool_calls", "function_call"}:
        raise ConversationBlocked(stop.reason)

      tool_calls = extract_tool_calls(outputs)

      with span("harness.gather"):
        results = await self.harness.gather(tool_calls)

      self.history.append(results)
      self.persist(results)
      self.emit("conversation.tool_results", results)

  def interrupt(self):
    self.stop_event.set()

  async def close(self):
    await self.harness.close()
```

因此 Conversation 的持久化粒度是 `InputMessage` / `GenMessage` / `ToolResult`，可观测性
粒度是一次 llm step 和一次 tool gather。Conversation 只在 `stop` / `interrupted` 结束；
`tool_calls` / `function_call` 表示继续执行工具并进入下一次 LLM step；其他 reason 不是
正常完成，需要作为阻塞状态暴露。打断不是删除 turn，而是让当前 LLM stream 尽快产出
`StreamStop(reason = interrupted)`，然后按已收到的输出正常持久化。cost 跟随
`StreamStop.usage` 记录；如果 provider 不给价格，则用本地 pricing estimator 补预估值。
Conversation 关闭时只释放自己拥有的 Harness；LLM client、Runtime、Integration 不归它管。

Actor 和 Conversation 一样是业务层。默认 Actor 通过等待 mailbox 获取输入，然后自行决定
如何创建和驱动 Conversation。除此之外，框架对 Actor 没有要求；子类可以任意编排。
`run` 是 Actor 对 yuubot 暴露的长驻接口。

Actor Manager 没有重型概念，只是一个 `dict[actor_id, Actor]`。管理接口通过 Actor 暴露。

```text
ActorConfig = {
  name: str,
  id: str,  # 首次创建时系统填写
  description: str,
  workspace: str,
  persona: str,
  tools: dict[name, config],  # 由系统推导
  mailbox: str,  # mailbox address, 系统分配
}
```

```py
class Actor:
  status = {idle, running, terminated, blocked}
  mailbox = Mailbox  # 和 runtime 中注册的是同一个 mailbox

  @classmethod
  def from_config(cls, config, runtime):
    mailbox = Mailbox()
    create_actor(..., mailbox)
    runtime.mailboxes[config.mailbox] = mailbox
    return actor

  async def spawn_conversation(self):
    pass

  async def run(self):
    pass

  async def close(self):
    pass
```

`from_config` 创建 Actor 并注册邮箱。`spawn_conversation` 创建一个隔离上下文的
Conversation；所有上下文在此时准备好。yuubot enable Actor 时，把 `actor.run()` 丢进一个
async task 里一直跑；disable Actor 时，取消 task 并调用 `actor.close()`。`run` 的内部编排
不受限制：Actor 子类可以串行、并发、复用 Conversation，或完全改写调度策略。

## Integration & Facade Context

Integration 接口主要是配置 / 上下文的约定。它不和可执行代码强绑定，否则代码库会变得
非常复杂且不灵活。Integration 使用 import path 指示 Python facade。

代码文件夹：

```text
src/
  yuubot  # 正常代码
  yb      # yuubot 提供的系统级能力，通过 execute_python 访问
  yext    # yuubot 提供的扩展能力，例如 integration，通过 execute_python 访问
```

Integration 代码放在固定 facade 子域：

```text
yext.xxx.yyy
```

系统不理解 Integration 内部函数，也不从 capability spec 生成 SDK。真正需要规定的
facade 协议只有三件事：

- `config_schema()`：声明前端要填的实时配置，例如 API key、repo、base URL。
- `session_context(config, runtime)`：产出初始化 Python session 时拼接的 context。
- `prompt(config, context)`：编译 system prompt 时贡献一小段说明。

加载时：

```py
record = load_record_from_integration_table()
integration_cls = registry.find(record.type)
config_schema = integration_cls.config_schema()
config = msgspec.convert(config_schema, record.config)
integration = integration_cls.from_config(config, runtime)
runtime.integrations[integration.name] = integration
```

`from_config` 是系统内部生命周期入口，用于把数据库 record 和 runtime 绑定成启用中的
Integration 实例；它不是模型可见的 capability，也不生成 Python facade SDK。

启动 Conversation，需要准备 system prompt 时：

```py
for integration in runtime.integrations.values():
  system_prompt += integration.prompt(config, context) + "\n\n"
```

执行 `execute_python`，需要跨进程传递上下文时：

```py
for integration in runtime.integrations.values():
  context["integrations"][integration.name] = integration.session_context(config, runtime)
```

新增 Integration 的最小设计：

```text
integration package
  config_schema()
  session_context(config, runtime) -> dict | code | files
  prompt(config, context) -> str

optional daemon side
  routes()
  rpc_handlers()
```

Inbound-only Integration 可以只实现 daemon side。Local-only Integration 可以完全没有 RPC。

## Extension Rule

- 元能力&写python反而更不方便的能力&多模态相关：写Tool。
- 新增“平台/服务连接/增强功能”：写 Integration。
- 需要跨进程能力：在 daemon 手写 RPC，别为它发明通用 capability spec。
