# Design: yuubot Core Backend Concepts

## Problem / Goal

这篇文章描述了一个优雅的LLM powered Bot架构。

目标是后端按照设计完善 & 前端 “配置，对话” 流走通。可使用真实${DEEPSEEK_API_KEY}测试（尽量少以防止账单刺客）。 

## Concept Map

`Runtime` 管理系统级资源：异步任务、数据库、缓存、Integration 实例、mailbox、
eventbus。

`ConversationContext` 是一次会话共享的只读上下文树。它把 model、conversation、actor、
workspace、otel、rpc、integrations 等信息交给当前会话下的所有单元。

`Actor` 是可被路由和管理的 agent 实体。Actor 持有 mailbox，并按配置创建隔离的
Conversation。

`Conversation` 是业务会话。它组合 LLM client、History helper，并在每次 loop 中组装
Harness，自行处理持久化与 cost。

`ConversationManager` 管理 Conversation 身份、History、状态与最后活跃时间。它按
`conversation_id` 查找或创建业务会话，并用 TTL 清理长时间不活跃的会话状态。

`Harness` 负责处理 Tool Call：找到 Tool、校验 payload、反序列化、并发执行、返回
ToolResult。

`Tool` 是模型直接调用的能力，例如 `read`、`edit`、`write`、`bash`、
`execute_python`。

`Integration` 是平台/服务连接。它主要规定配置、session context、prompt，以及必要时
的 daemon side route / RPC handler。

`execute_python` 是 agent 的 Python 扩展面。它运行本地 Python 代码，导入 `yb` 和
Integration facade；当本地能力不够时，可以通过手写 RPC 访问 daemon。

## Conversation Context

创建或重建 Conversation 时，yuubot 通过 helper 构造一套完整的上下文树。该 context
被当前会话下所有单元访问。

```text
ConversationContext = {
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

`ConversationContext` 是共享读模型，不是服务定位器。需要什么字段就显式挂上去；先让
builtin tools 和 Integration facade 跑通，再考虑更复杂的依赖注入。

ConversationContext 不是 `Runtime` 方法收集的结果，而是由独立 helper 构造：

```py
async def build_conversation_context(
  *,
  runtime: Runtime,
  actor_config: ActorConfig,
  conversation_id: str,
) -> ConversationContext:
  ...
```

## Runtime / Task / Mailbox

Runtime 管理所有系统资源。

```text
Runtime = {
  tasks: dict[task_id, Task],
  db: Database,
  cache: CachePool,
  integrations: dict[name, Integration],
  conversations: ConversationManager,
  mailboxes: dict[address, MailBox], ## address: 形如 `actor:actor_id`. 
  gateway: Gateway, ##分发消息
  eventbus: EventBus,
  listeners: ListenerHub,
  actors: dict[id, actor],
}


runtime.get_mailbox(id): get Mailbox lazily.
runtime.emit(event) 往eventbus发一个事件
```

`listeners` 持有所有 eventbus 消费者。进程启动时注册常驻 listener（如 Gateway、
TaskDelivery）；WebSocket 连接在客户端 subscribe 时动态注册 `WsListener`，断开时移除。
Conversation、TaskScheduler 等业务组件**只 emit 事件**，不直接碰 WebSocket。

```text
ListenerHub
  add(listener) / remove(listener)
  # 单 loop 订阅 eventbus，分发给已注册的 listener

Listener（协议）
  async def on_event(kind, payload) -> None

常驻 listener 示例：Gateway（incoming.message）、TaskDelivery（task.finished）
动态 listener 示例：WsListener（按连接上的 subscribe 命令过滤并 push WS frame）
```

`eventbus` 是事件出口，事件全部走这里发射。观测与业务副作用都经 listener 承接；新增能力时
注册新 listener，不扩展 `Runtime` 字段。

`tasks` 是所有异步 Task。可借由 tasks 进行任务管理：查询、增加、中断、删除。

`cache` 是所有需要缓存的部分使用的 cache pool。这是一个 LRU untyped cache，存储格式
为 `{key, meta, data}`，按 `data` 计算容量上限。

`integrations` 是所有启用的 Integration 实例。

`conversations` 管理 `conversation_id -> Conversation` 的短期索引；History 持久化在数据库中。

`mailboxes` 是 `{addr: MailBox}`。Actor 创建过程中会往这里注册 mailbox。

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

以上定义可通过 `LLMClient` 转换至各供应商 API。

完整 `History`（含 tool specs 与 system prompt）写入 `HistoryStore`，续聊时从库中加载并
经 `to_llm_input()` 直接交给 `LLMClient`，避免 actor / integration 配置变更后与已发送
前缀不一致（例如 provider prompt caching 失配）。对外暴露给前端的 conversation history
在 HTTP / WebSocket 边界剥离开头的 `tool_specs` / `system_prompt` 记录，只返回用户可见的
交互消息；`message_count` 等摘要字段同样按交互段计数。

`HistoryStore` 支持的 item kind：

```text
tool_specs      # 会话创建时写入的 OAI tool schema 列表
system_prompt   # 会话创建时写入的 system / developer 前缀
input           # InputMessage（user / developer）
gen_*           # GenMessage
tool_result     # ToolResult
```

`LLMClient` 是 provider adapter。它接收 yuubot 标准 History，产出 yuubot 标准
StreamEvent，并封装 provider 的请求格式、流式事件、usage、PAYG cost、account 信息。

```py
class LLMClient:
  @classmethod
  def from_config(cls, config, runtime):
    pass

  async def stream(
    self,
    history: History,
    *,
    model: ModelCard,
    context: ConversationContext,
    stop_event: Event,
  ) -> AsyncIterator[StreamEvent]:
    pass

  async def close(self):
    pass
```

`LLMClientConfig` 包含 provider、model、endpoint、api key ref 和 provider options。
`stream` 不执行 Tool，不持久化 History；它只负责一次 LLM 请求，以及请求结束时生成带
`usage` / `account` 的 `StreamStop`。

多模态 `ContentItem` 在 History 中只保存 `url` / `path` 和必要 metadata，不内联保存
base64。Provider 需要 base64、multipart 或其他格式时，由 `LLMClient` 在发送请求前转换。
转换结果属于派生数据，写入 `runtime.cache`；cache key 包含 content kind、path/url、
mtime/etag/size、mime、encoding version。cache miss 时重新读取原始内容并编码。这样
History 不会被 base64 打爆，多轮对话也不会反复编码同一份图片、音频或文件。

将流式 LLM 输出规范化的基石组件：

```py
chunks = [chunk async for chunk in llm.stream(history, model=model, context=context, stop_event=stop_event)]
outputs = merge(chunks, drop_partial_toolcall=True) -> list[GenReasoning | GenText | GenToolCall]
tool_calls = extract_tool_calls(outputs)
# results = harness.gather(tool_calls)
```

### Stream Protocol

LLM stream 产出：

```text
StreamEvent = (group_id, kind, payload)

Gen***Start(group_id)
Gen***Delta(group_id)
Gen***End(group_id)  # 除 ToolCall 以外
GenToolCallName(group_id)
GenToolCallArgumentsDelta(group_id)
GenToolCallArgumentsEnd(group_id)
ToolResultDelta(group_id)
ToolResultEnd(group_id)
StreamStop(reason, usage, account)
```

`group_id` 表示该 event 属于哪个输出分组。同一个 `GenText` / `GenReasoning` /
`GenToolCall` 的所有 start / delta / end 共用同一个 `group_id`。`merge` 按
`group_id` 聚合，得到完整的 `Gen*` 输出。

`GenToolCallName` 需要 adapter 完整等到名称，或从过量 stream events 中提取名称。
`GenToolCallArgumentsEnd` 是 `GenToolCall` 的结束事件。

Tool 执行也使用同一套 `StreamEvent` 外形向前端暴露过程输出：

- `tool_result_delta`：`group_id` 为真实 `tool_call_id`。payload 包含
  `tool_call_id`、`tool_name`、`text`。用于 bash stdout、execute_python stdout/stderr
  等长阻塞工具的过程输出。该事件不写入 History，只用于实时 UI。
- `tool_result_end`：`group_id` 同样为 `tool_call_id`。payload 包含
  `tool_call_id`、`tool_name`、`content`，其中 `content` 是最终 `ToolResult.content`
  的 wire 形态。Tool 正常完成、校验失败、执行异常、超时、中断时都必须发出该事件。

`tool_result_delta` 与 `tool_result_end` 属于 Conversation/Harness 层事件，不由
Provider 产生。`tool_result_end` 是流式视图的权威收束；随后仍可发
`conversation.tool_results` 批量事件，供持久化通知、旧观察者和运行时摘要使用。前端
收到同一 `tool_call_id` 的 completed result 时，必须用 completed 内容替换 running
delta 内容或去重，不能把过程输出和最终结果无条件拼接。

`reason` 透传 OpenAI `finish_reason`：`stop`、`length`、`tool_calls`、
`content_filter`、`function_call`；另加 yuubot 自己的 `interrupted`。

`usage` 包含 `input_tokens`、`cached_input_tokens`、`output_tokens`、
`PAYG_cost`。`account` 包含账户剩余情况：`credits`（美元计价）、`quota`（百分比额度）
以及 provider-specific 细节，无法统一。`account` 可以为空。`usage.PAYG_cost` 也可以
为空（provider 无法提供或查询失败）或 `0`（账号是订阅制）。这些细节由 `LLMClient`
provider adapter 自行填入；如果 provider 需要单独查询 usage、账单或账户状态，也由
`LLMClient` 在请求结束或打断后完成，并把结果放进最后的 `StreamStop`。

yuubot 自行维护一个统一的 pricing estimator，根据 tokens 计算价格，仅用于预估。
Conversation 记录 provider adapter 返回的 `usage` / `account`；当 `PAYG_cost` 为空时，
再用本地 pricing estimator 补一份预估价格。预估价格优先级低于 provider 返回值。

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

async for chunk in llm.stream(history, model=model, context=context, stop_event=stop_event):
  ... 正常消费

llm client:

yield from provider(e.g. oai) client
if stop_event.is_set():
  若可能，查询一次 provider 的账单接口，制作 StreamStop 块。reason 为 interrupted.
```

## Harness & Builtin Tools

处理 Tool Call 的框架称为 Harness。

```py
class Tool:
  payload_type: Type[msgspec.Struct]

  @classmethod
  def from_config(cls, config, runtime): ## 尽可能轻量化 & 将重行为推迟。例如execute_python将创建kernel推迟到首次execute
    pass

  async def execute(self, payload: Struct) -> str | Content:
    pass

  async def close(self): #释放资源
    pass

  @classmethod
  async def uninstall(cls, config, runtime):
    pass
```

`payload` 已反序列化。Tool 可以返回 `Content` 以处理多模态，例如 read image。Tool 允许
多并发，每次 `execute` 调用独立；若存在共享资源，需要自行处理并发竞争。`close` 清理
Tool 自己持有的 per-conversation 资源，例如子进程、session、临时文件。`uninstall`
不依赖 Tool 实例，用于按 config 清理 actor / workspace 级安装资产。

```py
HarnessConfig = {"tools": ..., "workspace": ...}

class Harness:
  @classmethod
  def from_config(cls, config, runtime):
    pass

  async def gather(self, tool_calls, stop_event, timeout=240) -> list[ToolResult]:
    pass

  async def close(self):
    for tool in self.tools.values():
      await tool.close()
```

Harness 初始化 Tools 并注册。`gather` 找到 Tool、validate 输入、反序列化，并发执行
tool calls，并施加 240s 硬超时。Tool 调用失败不抛出到 Conversation；Harness 总是合成
对应 `ToolResult` 交还模型：

- 模型传入的 JSON 无法通过校验 / 反序列化时，不执行 Tool，返回一条错误 `ToolResult`，
  明确告诉模型该 tool call 的 JSON 写错了。
- Tool 执行过程中抛出的任意异常都由 Harness 捕获，返回一条错误 `ToolResult`，内容包含
  可给模型阅读的错误信息。
- Tool 超过 240s 时，Harness 直接 cancel 执行任务，并返回一条系统 `ToolResult`：
  `[system] <tool name>工具调用已超过240s, 被强制中断.`。Tool 的 `execute` 可捕获取消
  异常，并通过 `asyncio.current_task()` 在当前 task 上挂载 `partial_result`；Harness
  cancel 后检查该值，若存在则追加：
  `该工具产生的临时result为：<partial result>`。

`stop_event` 被设置时，Harness 取消尚未完成的 tool task，并为被取消的调用返回
interrupted ToolResult。Harness 拥有 Tool 生命周期，关闭时依次关闭所有 Tool。

### Builtin Tools

`read` / `edit` / `write` / `bash` / `execute_python` 是普通 Tool：有 tool spec，也有
runtime-only config。

文件和 shell 工具主要查询 workspace context。`execute_python` 多消费一个跨进程 facade
context：`sys_path`、startup code、actor / session / mailbox identity、Integration
context。

read(path, start_lo=0, end_lo=-1) -> 全文（小于等于300行 and 小于等于 64KB） or 被截断文章 & 截断的最终位置。对于单行超大文本（例如压缩后的js），LLM应该自行用bash切分/格式化后再阅读。
edit(path, old_string, new_string): 精确字符串匹配。
write(path, content): 覆盖写。初始创建或者edit很麻烦时使用。
bash(command, timeout_s=None): 如terminal一样运行一条指令。可限制超时。该超时可任意设置，但超过框架时间也一样会被打断。

### execute_python

`execute_python` 是 yuubot 的核心工具。Agent 可通过它执行代码，体验类似 Jupyter：
上下文在一次 `Conversation.run_loop(input)` 内驻留，原生 `await`。

Prompt 里应原模原样告诉 Agent：这是 IPython 交互环境。IPython 自己已经有良好的异常展示
和恢复机制，因此 `execute_python` 不需要额外包复杂错误处理；让异常按 IPython 交互结果
返回即可。

> 实现用 `IPython.core.interactiveshell` 即可，因为不需要 UI。回收时，使用
> `shell.reset()` 或清空全局变量字典来回收用户变量，但是保持运行库 import，因为 import
> 通常非常慢。

`execute_python` 通过手写 SDK 扩展，等价于跑一个 Python 本地库。没有魔法：yuubot 会在
workspace 创建时编译并安装一份 Python 代码过去（安装的内容为yb, yext和pyproject.actor.toml里面内置的包）；如果这个 workspace 是新的，或没有其他 actor
共享，LLM 可以随时在自己的 workspace 安装新依赖。使用 uv 实现依赖共享，避免占用过多磁盘
空间。

一次用户输入触发一次 `run_loop`。`run_loop` 开头组装 Harness 和 Tool，loop 内多轮
LLM/tool call 共享同一个 Python kernel；最终回复、blocked、interrupted、error 后释放
Tool 和 kernel。用户续聊时会创建新的 kernel，并向 History 追加一条说明，告知模型上一次
Python 运行状态已经重置。

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
  imports yb / yext.*
  code calls yext.github.main.list_issues(...)
  integration code runs locally in python session
  optional: hand-written RPC to daemon when local is not enough
```

所以 Integration 的普通能力就是本地 Python 代码。daemon 只负责把实时配置和跨进程
context 拼好；需要 daemon 参与的能力，由该 Integration 自己定义一小段手写 RPC。

## Actor / Conversation

`Conversation` 是业务概念。它使用 LLM client、History helper，并在 loop 中组装
Harness，自行处理持久化 / cost。

```text
Conversation = {
  id: str,
  context: ConversationContext,
  history: HistoryHelper,
  llm: LLMClient,
  stop_event: Event,
}
```

Conversation 是业务编排层：它不实现 Tool，也不理解 Integration 内部能力；
它只把 History、LLM step、Harness、持久化、可观测性、打断语义串起来。`HistoryStore`
保存完整 History（含开头的 `tool_specs` 与 `system_prompt`）；续聊时 `HistoryHelper` 从库加载
全文，`to_llm_input()` 还原出发给模型的输入。HTTP / WebSocket 返回给前端的 history 在
facade 层去掉前缀，只展示交互段。Harness 和 Tool 属于一次 `run_loop(input)`，在 loop
开始时创建，在 loop 结束时关闭。

```py
class Conversation:
  async def run_loop(self, input): #input 的类型是InputMessage，由调用方进行包装
    self.stop_event.clear()
    self.history.append(input)
    self.persist(input)
    self.emit("conversation.input", input)

    harness = Harness.from_config(self.config.harness, self.runtime) 
    ## harness config已经反序列化，无需二次读db，避免开销。
    try:
      while not self.stop_event.is_set():
        with span("llm.step"):
          chunks = [
            chunk
            async for chunk in self.llm.stream(
              self.history.to_llm_input(),
              model=self.context.model,
              context=self.context,
              stop_event=self.stop_event,
            )
          ]
          for chunk in chunks:
            if chunk.kind != "stream_stop":
              self.emit("conversation.stream", conversation_id=self.id, event=chunk)
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
          results = await harness.gather(tool_calls, self.stop_event)

        self.history.append(results)
        self.persist(results)
        self.emit("conversation.tool_results", results)
    finally:
      await harness.close()

  def interrupt(self):
    self.stop_event.set()

  async def close(self):
    self.stop_event.set()
```

因此 Conversation 的持久化粒度是完整 History item：`tool_specs`、`system_prompt`、
`InputMessage` / `GenMessage` / `ToolResult`。对外 API 的 `message_count` 与 history 列表
不计入前缀。可观测性粒度是一次 llm step 和一次 tool gather。Conversation 只在 `stop` /
`interrupted` 结束；
`tool_calls` / `function_call` 表示继续执行工具并进入下一次 LLM step；其他 reason 不是
正常完成，需要作为阻塞状态暴露。打断不是删除 turn，而是让当前 LLM stream 尽快产出
`StreamStop(reason = interrupted)`，或让当前 Harness 取消未完成的 tool task，然后按已
收到的输出和 tool result 正常持久化。cost 跟随 `StreamStop.usage` 记录；如果 provider
不给价格，则用本地 pricing estimator 补预估值。Conversation 关闭时只打断当前 loop；
LLM client、Runtime、Integration 不归它管。

Actor 和 Conversation 一样是业务层。Actor 只承诺可被运行、关闭、管理；它内部是否使用
LLM、是否创建 Conversation、是否复用 Conversation，均不是框架约束。默认 Actor 通过
等待 mailbox 获取输入，然后创建和驱动 Conversation，但这只是默认实现。`run` 是 Actor
对 yuubot 暴露的长驻接口。

Actor Manager 没有重型概念，只是一个 `dict[actor_id, Actor]`。管理接口通过 Actor 暴露。

```text
ActorConfig = {
  name: str,
  id: str,  # 首次创建时系统填写
  description: str,
  workspace: str,
  persona: str,
  tools: dict[name, config],  # 由系统推导
}
```

```py
class Actor:
  status = {idle, running, terminated, blocked}
  mailbox = Mailbox  # 和 runtime 中注册的是同一个 mailbox

  @classmethod
  def from_config(cls, config, runtime):
    mailbox = runtime.get_mailbox(config.id)
    create_actor(..., mailbox)
    return actor

  async def spawn_conversation(self, conversation_id=None):
    pass

  async def run(self):
    pass

  async def close(self):
    pass
```

Conversation 初始化示例：

```py
class Actor:
  async def spawn_conversation(self, conversation_id=None):
    conversation_id = conversation_id or new_id()
    context = await build_conversation_context(
      runtime=self.runtime,
      actor_config=self.config,
      conversation_id=conversation_id,
    )
    return Conversation.from_config({
      "context": context,
      "llm": self.config.model,
      "history": {"conversation_id": conversation_id},
      "harness": {"workspace": self.workspace, "tools": self.config.tools},
    }, runtime=self.runtime)

class Conversation:
  @classmethod
  def from_config(cls, config, runtime):
    return cls(
      context=config["context"],
      history=HistoryHelper.from_config(config["history"], runtime),
      llm=LLMClient.from_config(config["llm"], runtime),
      config=config,
      runtime=runtime,
      stop_event=Event(),
    )

class ExecutePython(Tool):
  @classmethod
  def from_config(cls, config, runtime):
    facade = runtime.prepare_facade(config["workspace"])
    return cls(facade)

  @classmethod
  async def uninstall(cls, config, runtime):
    await runtime.remove_facade(config["workspace"])
```

`Conversation.from_config` 构造 history / llm client，并保留 harness config；
`run_loop` 开始时调用 `Harness.from_config` 递归初始化各 Tool。`execute_python` 构造时调用幂等的
`runtime.prepare_facade(workspace)`，已有 facade venv 时直接复用。默认 Actor 可以在创建后
立刻 spawn 一个 Conversation 来预热 facade，效果类似提前 import（可节省约8s）；这只是
编程技巧，不是 Actor 约束。Actor disable 只停任务；remove / uninstall 时按 ActorConfig
递归调用 tools 的 `uninstall(config, runtime)`，由 `execute_python` 自己清理该 workspace
的 facade venv。

`from_config` 创建 Actor 并注册邮箱。`spawn_conversation` 创建一个隔离上下文的
Conversation；所有上下文在此时准备好。yuubot enable Actor 时，把 `actor.run()` 丢进一个
async task 里一直跑；disable Actor 时，取消 task 并调用 `actor.close()`。`run` 的内部编排
不受限制：Actor 子类可以串行、并发、复用 Conversation，或完全改写调度策略。

## Conversation Manager

ConversationManager 提供 `get_or_create(actor, conversation_id)`。有 `conversation_id`
时加载对应 History 并复用活跃 Conversation；没有时创建新的 Conversation id 和 History。
创建新对象时由 Actor 执行 `spawn_conversation(conversation_id)`。
每次用户输入、LLM 输出、tool result 都会刷新最后活跃时间。后台清理任务定期关闭超过 TTL
未活跃的 Conversation 对象；History 不因 TTL 删除。

用户续聊命中已有 History 时，Actor 重新进入 `run_loop(input)`。新的 loop 会重新组装
Harness 和 Tool；如果上一次 loop 使用过 `execute_python`，History 会追加一条 developer
消息，说明 Python kernel 已重置，之前的变量、import、副作用状态不可继续依赖。

## Integration & Facade Context

Integration 接口主要是 daemon side 配置 / 生命周期的约定。它不理解 facade 内部函数，也
不和可执行代码强绑定，否则代码库会变得非常复杂且不灵活。Integration record 使用
package path 指示 Python facade；系统 prompt 直接读取该 module doc。这是 daemon side
Integration 和 Python facade 唯一的静态关联。

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
facade 协议只有两件事：

- `config_schema()`：声明前端要填的实时配置，例如 API key、repo、base URL。
- `session_context(config, runtime)`：产出初始化 Python session 时拼接的 context。

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
for integration_record in enabled_integrations:
  facade_module = import_module(integration_record.package_path)
  system_prompt += inspect.getdoc(facade_module) + "\n\n"
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

optional daemon side
  routes()
  rpc_handlers()
```

Inbound-only Integration 可以只实现 daemon side。Local-only Integration 可以完全没有 RPC。
RPC 是 facade 自己发起的普通 daemon HTTP 调用；认证、授权、token 传递属于该 RPC 的实现
细节，不进入统一 capability model。

## Data Layout

这一节规定了yuubot的数据存储规范。所有数据存放于 config yaml指定的 data dir下。结构为

```
data_dir/
  workspace/ #为actor分配workspace的地方
    amy/
      .agents/skills/ #本地skills文件夹; 按照 https://agentskills.io/client-implementation/adding-skills-support 规范加载
      artifacts/ ## 用于向用户展示产物的文件夹
      uploads/<mime>/ #用户上传文件目的地
      projects/ ## actor自行维护的项目目录
      notes/ ## actor记录的笔记
      scripts/ ## 一些方便的脚本，例如清理过多的垃圾uploads、写note（自动追加元数据）之类的
      AGENTS.md ## 各文件夹解释、路径map
  published/{share_id}/  ## 公网 Share 快照；见 design/deployment/deployment-design.md
  kv/{actor_id}/         ## Actor JSON KV；见 design/services/05-kv.md
  logs/ # rotate logs
  db/  ##database相关。shm/wal等数据自动放在这下面
    yuubot.db # database
```

## Extension Rule

- 元能力&写python反而更不方便的能力&多模态相关：写Tool。
- 增强功能、内置级能力：直接扩展yb. 如pdf CRUD, 等。
- 新增“平台/服务连接”：写 Integration。
- 需要跨进程能力：在 daemon 手写 RPC，别为它发明通用 capability spec。

## 示例1. 配置-用户Out-of-the-Box

用户在前端配置预设provider的api-key。在actors页面选择创建预设Actor（使用配好的provider）。点击对话，进入Conversation页面。开始对话。通过Stream协议流式渲染。

## 示例2. 配置-高级配置

1. LLM Providers配置预设/自定义端点，填写api protocol，url, api key
2. Integrations连接Integration，填写config推导出的表单（前端可以特判渲染），保存 -> 变为可编辑、可enable/disable卡片。此时所有Actor获得相关调用能力。
3. 新建Actor, 编辑persona, 填写workspace路径, 选择provider & model. 
4. 已有Actor的情况下，新增Integration/disable Integration，actor的execute_python仍然可以调用（因为代码已经生成过了），但是daemon上相关的service会停掉，导致调用自动失败（这是一个“自然”发生的过程）。

## 示例3. Conversation对话

1. 选择配好的Actor对话 -> 进入New页面（此时还没有Conversation）
2. 用户首条消息：后端把 actor 和 conv-id 交给 ConversationManager。若有，复用；若无，
  创建一个。
  创建Conversation: Actor spawn conversation
3. Conversation追加用户消息，跑一轮loop.
4. 用户打断：设置stop event
5. 前端收到同款后端流事件，渲染append-only view
6. 用户发新消息：重复。

## 示例4. Actor夜晚无人值守

该示例较为复杂。以QQ为例。

1. 配置QQ Integration，登录账号（如Napcat等）。
2. 配置qq群/私聊 到 特定actor的route. (<type>:<pattern> -> actor格式。如 qq.group.id:232411 amy; `qq.group.name:*acg*`(所有含acg字符群名) amy). 系统在保存时会将选中actor解析为actor id，再借助runtime倒查mailbox. 
3. QQ Integration会启动一个服务，监听websocket请求。
4. 监听到符合route的消息时，QQ Integration向Runtime emit一条IncomingMessage（含route）。Runtime.gateway监听eventbus、匹配route，命中记录，投递至相关mailbox（若有，没有就丢弃；暂时不做消息积压）。
5. Actor正阻塞读取mailbox. 此时有新消息，trigger，进入循环。出站消息通过yext.qq sdk发送，如 `group = yext.qq.Group(id=....); group.send([yext.qq.at(123), "hello"])`.
