# Lifecycle Design: yuubot Core Backend

本文补充 `design.md` 的生命周期部分：配置进入系统、数据库状态迁移、运行态资源构造、
业务对象启停、Conversation 持久化、缓存刷新和关闭清理。

**禁止依赖泄露**

## Lifecycle Boundaries

yuubot 有四个生命周期边界：

- `DeploymentConfig`：进程启动输入，包含 data dir、server 和进程级策略。
- `ApplicationState`：数据库内的业务状态，包含 LLM、Integration、Actor、Route、
  Conversation、History、Cost 和 schema version。
- `Runtime`：进程内资源 owner，持有 database、cache、eventbus、gateway、mailbox、
  task、Integration、Actor、ConversationManager 和 provider client。
- `ConversationContext`：一次 Conversation 的只读上下文树，携带 model、conversation、
  actor、workspace、otel、rpc 和 integrations。

`Runtime` 负责把 durable record 转成 typed config，再构造运行态 object。
`Actor`、`Conversation`、`Harness`、`Tool` 和 `Integration` 消费 typed config 与 runtime
接口。

## State Shapes

```ts
DeploymentConfig = {
  schema_version: number = 1
  data_dir: Path
  server: { host: string = "127.0.0.1", port: number = 8765 }
  admin_url_base: string
  public_url_base: string
  trusted_proxies?: string[]
  admin_auth?: AdminAuthConfig
  secrets?: SecretPolicy
}
```

`admin_url_base`、`public_url_base`、`admin_auth` 见
[`deployment/deployment-design.md`](deployment/deployment-design.md)。

ApplicationState = {
  schema_version: number
  llms: Record<string, LLMRecord>
  integrations: Record<string, IntegrationRecord>
  actors: Record<string, ActorRecord>
  routes: RouteRecord[]
  shares: Record<string, ShareGrant>
  conversations: Record<string, ConversationRecord>
  histories: AppendOnlyLog<HistoryItemRecord>
  costs: AppendOnlyLog<CostRecord>
}
```

启动器读取 `DeploymentConfig`，迁移 schema，打开 `data_dir` 下的 database 和运行态目录。
Runtime 读取并迁移 `ApplicationState`。管理 API、迁移脚本和 preset installer 创建或修改
业务 record。

```ts
LLMRecord = {
  id: string
  provider: string
  name: string
  model: string
  endpoint?: string
  api_key_ref?: string
  options: JsonObject = {}
  schema_version: number = 1
  last_error?: LifecycleErrorRecord
  updated_at: Timestamp
}

IntegrationRecord = {
  id: string
  type: string
  name: string
  enabled: boolean
  package_path?: string
  config: JsonObject
  schema_version: number
  last_error?: LifecycleErrorRecord
  updated_at: Timestamp
}

ActorRecord = {
  id: string
  type: string
  name: string
  description: string
  enabled: boolean
  config: JsonObject
  schema_version: number
  status: "idle" | "running" | "blocked" | "terminated" | "disabled"
  last_error?: LifecycleErrorRecord
  updated_at: Timestamp
}

RouteRecord = {
  id: string
  integration_id: string
  pattern: string
  actor_id: string
  enabled: boolean
  schema_version: number
}
```

LLM、Integration、Actor 和 Tool 都通过 registry 解析显式 `type`。Route 由 gateway 用于
入站事件到 Actor mailbox 的投递。

```ts
ToolRecord = {
  type: string
  enabled: boolean = true
  config: JsonObject = {}
  schema_version: number = 1
}

ConversationRecord = {
  id: string
  actor_id: string
  status: "active" | "blocked" | "interrupted" | "closed"
  created_at: Timestamp
  last_active_at: Timestamp
  last_error?: LifecycleErrorRecord
}

HistoryItemRecord = {
  conversation_id: string
  seq: number
  kind: "tool_specs" | "system_prompt" | "input" | "gen_text" | "gen_reasoning" | "gen_tool_call"
    | "gen_image" | "gen_audio" | "tool_result"
  payload: JsonObject
  created_at: Timestamp
}

新会话创建时，在首条用户消息之前 append `tool_specs`（若有 tools）与 `system_prompt`。
续聊从 `HistoryStore` 加载完整序列；`to_llm_input()` 不再根据当前 Actor 配置重算前缀。
HTTP / WebSocket 的 conversation history 在 facade 层跳过开头的 `tool_specs` /
`system_prompt`，`message_count` 仅统计交互段。

CostRecord = {
  conversation_id: string
  seq: number
  usage: UsageRecord
  account?: AccountRecord
  estimated: boolean
  created_at: Timestamp
}
```

Conversation History 和 Cost 是 append-only。多模态 History 保存 path、url 和 metadata；
provider-specific 编码进入 cache。

## Typed Config

Runtime 是 record 到 config 的边界。

```py
class Runtime:
  def config_from_record(self, registry, record):
    cls = registry.resolve(record.type)
    return cls.config_type().load(
      metadata=record.metadata(),
      payload=record.config,
    )

  async def construct_from_record(self, registry, record):
    cls = registry.resolve(record.type)
    config = self.config_from_record(registry, record)
    return await cls.from_config(config, self)
```

```ts
LLMConfig = {
  id: string
  provider: string
  name: string
  model: string
  endpoint?: string
  credentials: RuntimeSecretRef
  options: LLMProviderConfig
}

IntegrationConfig = {
  id: string
  type: string
  name: string
  package_path?: Path
  params: IntegrationTypedConfig
}

ToolConfig = {
  name: string
  type: string
  enabled: boolean
  params: ToolTypedConfig
}

HarnessConfig = {
  tools: Record<string, ToolConfig>
}

ActorConfig = {
  id: string
  type: string
  name: string
  description: string
  enabled: boolean
  workspace: Path
  llm: LLMConfig
  model: ModelCard
  harness: HarnessConfig
  params: ActorTypedConfig
}
```

新增 provider、Integration、Actor subtype 或 Tool 时，新增 registry entry、config schema
和 factory。

## Runtime

```ts
Runtime = {
  config: DeploymentConfig
  db: Database
  cache: CachePool
  eventbus: EventBus
  gateway: Gateway
  listeners: ListenerHub
  tasks: Record<string, TaskState>
  mailboxes: Record<string, Mailbox>
  integrations: Record<string, Integration>
  actors: Record<string, Actor>
  conversations: ConversationManager
  registries: {
    llms: Registry<LLMClient>
    integrations: Registry<Integration>
    actors: Registry<Actor>
    tools: Registry<Tool>
  }
}

TaskState = {
  id: string
  owner: string
  status: "pending" | "running" | "cancelled" | "failed" | "done"
  stdin: TextStream
  stdout: TextStream
  result?: JsonObject
  error?: LifecycleErrorRecord
}
```

Runtime 创建 task、更新状态、传播错误，并在 disable 或 shutdown 时取消 owner 相关 task。
Runtime 常用接口：

```py
class Runtime:
  def get_mailbox(self, address) -> Mailbox: ...
  def emit(self, event) -> None: ...
```

## Startup

```py
async def startup(config_path):
  deployment = migrate_config(load(DeploymentConfig, from_=config_path))
  registries = create_registries()
  db = await Database.open(deployment.data_dir / "db")
  runtime = Runtime(deployment, db, registries)

  app_state = await runtime.load_and_migrate_application_state()

  runtime.listeners.add(GatewayListener(runtime.gateway, app_state.routes))
  runtime.listeners.add(TaskDeliveryListener(runtime))
  runtime.listeners.start()

  for record in app_state.integrations.enabled():
    await runtime.enable_integration(record.id)

  for record in app_state.actors.enabled():
    await runtime.enable_actor(record.id)

  await runtime.conversations.start_background_cleanup()
  return runtime
```

Startup 顺序是配置迁移、registry 创建、database 打开、Runtime 创建、ApplicationState
迁移、ListenerHub 注册常驻 listener 并 start、Integration enable、Actor enable、Conversation
cleanup task 启动。
Integration 构造失败记录 `last_error`。Actor 构造失败标记 `blocked` 并记录 `last_error`。

## Integration Lifecycle

```py
async def enable_integration(runtime, integration_id):
  record = await runtime.load_integration_record(integration_id)
  integration = await runtime.construct_from_record(
    runtime.registries.integrations,
    record,
  )
  await integration.start_if_needed()
  runtime.integrations[record.id] = integration
  await runtime.persist_integration_enabled(record.id, last_error=None)
  runtime.cache.invalidate(prefix=f"integration:{record.name}:")

async def disable_integration(runtime, integration_id):
  record = await runtime.load_integration_record(integration_id)
  integration = runtime.integrations.pop(record.id, None)
  if integration:
    await integration.close()
  await runtime.persist_integration_disabled(record.id, last_error=None)
  runtime.cache.invalidate(prefix=f"integration:{record.name}:")
```

Integration 拥有 daemon route、RPC handler、socket、background task 和 session context
生成。Re-enable 重新读取 record 并创建 fresh instance。

## Actor Lifecycle

```py
async def enable_actor(runtime, actor_id):
  record = await runtime.load_actor_record(actor_id)
  actor = await runtime.construct_from_record(runtime.registries.actors, record)
  actor.attach_mailbox(runtime.get_mailbox(f"actor:{record.id}"))

  if actor.supports_prewarm:
    await actor.prewarm_conversation(purpose="prewarm.actor")

  runtime.actors[record.id] = actor
  runtime.create_task(owner=record.id, coro=actor.run)
  await runtime.persist_actor_running(record.id, last_error=None)

async def disable_actor(runtime, actor_id):
  actor = runtime.actors.pop(actor_id, None)
  if actor:
    await runtime.cancel_tasks(owner=actor_id)
    await actor.close()
  await runtime.conversations.close_for_actor(actor_id)
  await runtime.persist_actor_disabled(actor_id, last_error=None)

async def remove_actor(runtime, actor_id):
  await runtime.disable_actor(actor_id)
  record = await runtime.load_actor_record(actor_id)
  config = runtime.config_from_record(runtime.registries.actors, record)

  for tool_config in config.harness.tools.values():
    tool_cls = runtime.registries.tools.resolve(tool_config.type)
    await tool_cls.uninstall(tool_config.params, runtime)

  await runtime.discard_actor_record(actor_id)
```

默认 Actor 等待 mailbox 输入，创建或恢复 Conversation，并驱动 `run_loop`。Prewarm 是昂贵
Tool 初始化的优化点。Actor remove 负责 Tool 安装资产清理；Conversation History 按产品保留
策略处理。

## Conversation Context

```ts
ConversationContext = {
  model: ModelCard
  conversation_id: string
  integrations: Record<string, IntegrationContext>
  actor: string
  otel: JsonObject
  workspace: Path
  rpc: JsonObject
}
```

```py
async def build_conversation_context(
  *,
  runtime: Runtime,
  actor_config: ActorConfig,
  conversation_id: str,
):
  integration_context = {
    integration.name: integration.session_context(
      actor_config=actor_config,
      conversation_id=conversation_id,
    )
    for integration in runtime.integrations.values()
  }
  return ConversationContext(
    model=actor_config.model,
    conversation_id=conversation_id,
    integrations=integration_context,
    actor=actor_config.id,
    workspace=actor_config.workspace,
    otel=current_trace_context(),
    rpc=create_rpc_context(actor_config, conversation_id),
  )
```

ConversationContext 在 Conversation object 创建或重建时由
`build_conversation_context(...)` helper 构造。helper 接收 `runtime`、`actor_config`、
`conversation_id` 等输入，并读取当前 enabled Integration 集合。已经创建的 Conversation
持有自己的 context。

## Conversation Lifecycle

```ts
ConversationSeed = {
  id: string
  actor_id: string
  history: HistoryHelper
  costs: CostTracker
  status: "active" | "blocked" | "interrupted" | "closed"
}

Conversation = {
  id: string
  context: ConversationContext
  history: HistoryHelper
  llm: LLMClient
  costs: CostTracker
  harness: HarnessConfig
  runtime: Runtime
  stop_event: Event
}
```

Conversation creator 可以是 Actor，也可以是 Manager 的 load/rebuild 流程。Creator 负责
准备 `conversation_id`、initial History、CostTracker、LLMClient、HarnessConfig 和
ConversationContext，然后构造 `Conversation`。

```py
async def create_conversation(runtime, actor_config, seed):
  context = await build_conversation_context(
    runtime=runtime,
    actor_config=actor_config,
    conversation_id=seed.id,
  )
  return Conversation(
    id=seed.id,
    context=context,
    history=seed.history,
    llm=await LLMClient.from_config(actor_config.llm, runtime),
    costs=seed.costs,
    harness=actor_config.harness,
    runtime=runtime,
    stop_event=Event(),
  )

class Actor:
  async def spawn_conversation(self, seed):
    return await create_conversation(self.runtime, self.config, seed)

async def rebuild_conversation(manager, actor_config, conversation_id):
  seed = await manager.runtime.load_conversation_seed(
    actor_id=actor_config.id,
    id=conversation_id,
  )
  conversation = await create_conversation(manager.runtime, actor_config, seed)
  manager.put(seed.id, conversation)
  return conversation
```

```py
async def get_or_create(manager, actor, conversation_id=None):
  conversation = manager.find(conversation_id)
  if conversation:
    return conversation

  seed = await manager.runtime.load_or_create_conversation_seed(
    actor_id=actor.id,
    id=conversation_id,
  )
  conversation = await actor.spawn_conversation(seed)
  manager.put(seed.id, conversation)
  return conversation
```

`Actor.spawn_conversation(seed)` 和 Manager rebuild 都使用同一个 creation contract：先调用
`build_conversation_context(...)`，再用 initial durable helpers 构造 Conversation。`ConversationManager`
拥有 active Conversation index 和 TTL cleanup。`HistoryStore` 与 `CostTracker` 拥有 durable
append-only log。

```py
async def run_loop(conversation, input_message):
  conversation.stop_event.clear()
  await conversation.append_persist_emit(input_message)
  harness = await Harness.from_config(conversation.harness, conversation.runtime)

  try:
    while not conversation.stop_event.is_set():
      chunks = await collect(conversation.llm.stream(
        conversation.history.to_llm_input(),
        model=conversation.context.model,
        context=conversation.context,
        stop_event=conversation.stop_event,
      ))
      outputs, stop = merge(chunks)
      await conversation.append_persist_emit(outputs)
      await conversation.record_cost(stop.usage)

      if stop.reason in {"stop", "interrupted"}:
        await conversation.mark_status(
          "interrupted" if stop.reason == "interrupted" else "closed"
        )
        return outputs

      if stop.reason in {"tool_calls", "function_call"}:
        results = await harness.gather(
          extract_tool_calls(outputs),
          conversation.stop_event,
        )
        await conversation.append_persist_emit(results)
        continue

      await conversation.mark_blocked(LifecycleErrorRecord(reason=stop.reason))
      raise ConversationBlocked(stop.reason)
  finally:
    await harness.close()
```

Conversation 持久化粒度是完整 History item。打断流程保留已经 append 的输出和 cost。
对外 history API 与 `conversation.history.append` 事件不包含开头的 `tool_specs` /
`system_prompt`。TTL cleanup 调用 `conversation.close()` 释放 runtime object。

## Harness, Tool, execute_python

```py
class Harness:
  @classmethod
  async def from_config(cls, config, runtime):
    tools = {}
    for name, tool_config in config.tools.enabled():
      tool_cls = runtime.registries.tools.resolve(tool_config.type)
      tools[name] = await tool_cls.from_config(tool_config, runtime)
    return cls(tools=tools)

  async def gather(self, tool_calls, stop_event, timeout=240): ...

  async def close(self):
    for tool in reversed(self.tools.values()):
      await tool.close()

class Tool:
  payload_type: Type[msgspec.Struct]
  async def execute(self, payload): ...
  async def close(self): ...

  @classmethod
  async def from_config(cls, config, runtime): ...

  @classmethod
  async def uninstall(cls, config, runtime): ...
```

Harness 是 per-`run_loop` object。它校验 tool-call JSON、反序列化 payload、并发执行 Tool、
施加 timeout，并把 success、validation error、execution error、timeout 和 interrupt 都转为
`ToolResult`。

`execute_python` 使用同一套 Tool lifecycle。它按自己的 `ToolConfig.params` 和当前
`ConversationContext` 自行准备运行资产。per-loop runtime resource 由它自行选择创建时机，Harness close 时关闭或
reset。Actor remove 时，Runtime 调用 `Tool.uninstall`，由 `ExecutePython` 清理它拥有的
workspace-level 安装资产。

## Cache

```ts
CacheEntry = {
  key: string
  meta: JsonObject
  data: bytes
  size: number
  expires_at?: Timestamp
}
```

Cache 是 runtime-only derived data。LLM provider content encoding、Integration 派生数据和
Tool helper 使用 namespaced key。File edit/write invalidates path-derived keys；
Integration enable/disable invalidates its namespace；shutdown clears cache。

## Reload, Migration, Shutdown

Reload 处理进程级配置，例如 logging、secret policy 和 gateway 参数。业务对象变化使用
create、enable、disable、remove 生命周期。

```py
async def load_and_migrate_application_state(runtime):
  state = await runtime.load_raw_application_state()
  while state.schema_version < CURRENT_SCHEMA:
    migration = migrations[state.schema_version]
    state = migration.apply(state, runtime.registries)
    await runtime.persist_migration_checkpoint(state)
  if state.schema_version > CURRENT_SCHEMA:
    raise UnsupportedSchemaVersion(state.schema_version)
  return state
```

Migration 处理 durable record shape。Registry-backed record 保留 `type`；subtype config
migration 属于 subtype owner。

```py
async def shutdown(runtime):
  runtime.listeners.stop()

  for actor in reversed(runtime.actors.values()):
    await runtime.cancel_tasks(owner=actor.id)
    await actor.close()

  await runtime.conversations.close_all()

  for integration in reversed(runtime.integrations.values()):
    await integration.close()

  await runtime.cancel_all_remaining_tasks()
  runtime.cache.clear()
  await runtime.eventbus.close()
  await runtime.db.close()
```

Shutdown 顺序是 ingress、Actor task、active Conversation、Integration resource、remaining
task、cache、eventbus、database。Shutdown preserves durable data。

## Failure And Retry

```ts
LifecycleErrorRecord = {
  code: string
  message: string
  detail?: JsonObject
  retryable: boolean
  occurred_at: Timestamp
}
```

Construction failure 写入 owner record 的 `last_error`。Runtime task failure 更新
`TaskState`，并传播到 durable task owner。Conversation blocked reason 写入
`ConversationRecord`。

Retry paths:

- Re-enable Integration reloads `IntegrationRecord` and constructs a fresh instance.
- Re-enable Actor reloads `ActorRecord` and starts a fresh Actor task.
- New Conversation input rebuilds runtime Conversation state from History and current
  ConversationContext.
- Tool execution retry is expressed as another model or user turn.
- Cache miss rebuilds derived data.

Lifecycle rollback boundaries:

- Successful enable installs runtime child handle and clears `last_error`.
- Failed enable preserves durable record and records `last_error`.
- Interrupted Conversation preserves appended History and Cost records.
- Failed Actor remove preserves Actor record with `last_error`.
