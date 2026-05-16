# 01. 系统分层与术语

## 总体定位

yuubot 是一个以 LLM Agent 为核心的多渠道 bot / personal AI platform。v2 是一次 breaking architecture rewrite：目标不是把当前实现包一层兼容壳，而是建立清晰、可读、类型明确、便于接入三方服务的长期架构。

中期目标是：用户通过 Admin UI 配置 Integration、Character、Actor，先在 Web 这个最小内置 Channel 中稳定使用 Actor Runtime；Discord、Telegram、QQ/NapCat 等渠道作为 Integration 接入，不进入 v2 core 的必要范围。

## 设计原则

- 代码路径按领域边界组织：Gateway 处理消息路由，ResourceService 处理资源写入，Actor Runtime 处理会话执行，LLM Backend 处理模型后端 infra 配置，Integration 处理外部连接和能力扩展。
- 模块之间传递 typed model，不传原始平台事件、散装 YAML dict 或隐式全局配置。
- 新增三方服务必须走 Integration 模型；新增 LLM 厂商必须走 yuuagents LLM backend contract。不能为了某个服务或平台在 runner、prompt、UI、配置加载里分别 patch。
- 优先选择能被 reviewer 快速理解的小而明确的接口；避免兼容层、双路径 fallback、平台专用主路径和历史命名污染核心模型。
- v2 core 可以移除 QQ / NapCat / OneBot adapter。核心完整性来自稳定边界和容易扩展，不来自内置很多平台。
- **平台不持有 IM 概念。** 会话隔离（thread/conversation/group）是 Integration 或 GroupActor 的内部实现细节，不属于平台抽象层。

## 分层架构

```text
External Platforms (Discord, Telegram, QQ, Linear, GitHub, ...)
        |
        v
Integrations
   协议转换：外部格式 -> IncomingMessage
   连接管理：webhook 注册、WebSocket 维持、轮询等
   能力暴露：via capability manifest 给 Agent 使用
         |
         v  (gateway.open_integration → IntegrationIngress)
Gateway
   按 ActorIngressRule 匹配 MessageSource → 将消息投递到目标 Actor mailbox
        |
        v
Actor Runtime
  Character + yuuagents AgentDefinition + model binding + tools + memory + policy
        |
        +------------------------------+
        |                              |
        v                              v
Runtime Resources DB             Infrastructure
  llm_backends, integrations,    yuuagents, trace, bridge, filesystem, docker
  characters, actors,
  actor_ingress_rules
```

## 核心对象

### LLM Backend

LLM Backend 是 Actor Runtime 的模型后端，也是可运行 Actor 的必要条件。OpenAI、Anthropic、Gemini、DeepSeek、OpenAI-compatible、Ollama 或本地模型服务都属于这一类。

LLM Backend 存在于 DB（`llm_backends` 表），secret 加密存储，支持在线创建、测试、禁用和切换。但**概念上它属于 daemon infrastructure**：它的作用是配置 yuuagents 的 `StageConfig.llm`，不是 agent 扩展点。它只服务模型绑定、模型目录、价格/预算和 chat/stream client，不生成 agent-visible 工具。Actor Runtime 只依赖 yuuagents LLM client contract，不把 vendor special case 写进 agent loop。

### yuuagents Infrastructure

`yuuagents` 是 yuubot daemon 使用的执行基建。它提供 `Stage`、`Actor`、`AgentDefinition`、tool executor 和 agent loop。它的 provider wiring（例如 `ipykernel`、`background`、`schedule`、`bash`、`fileop`）属于 daemon bootstrap/infrastructure config：管理员手动配置后重启 daemon 生效。

yuubot 的 LLM Backend 虽然在 DB 中热更新，但本质也是 infrastructure config——只是提升到 Runtime Resource 以便在线切换模型和密钥。Actor 持久化尽量贴近 `yuuagents.AgentDefinition` 的字段，启动时只做机械拼装。

### Integration

Integration 是 yuubot 与外部世界的连接点。它同时承担三个职责：

1. **协议转换**：将外部平台的消息格式转换为 `IncomingMessage`。
2. **连接管理**：管理 webhook 注册、WebSocket 连接、轮询等外部通信。
3. **能力暴露**：通过 capability manifest 向 Agent 暴露可调用的操作（如 `search.query`、`issue.search`）。

Integration 有三层结构：

```text
Loader (代码级)
  注册在 IntegrationFactoryRegistry 中，启动时加载。
  未启用的 Integration 也有对应的 Loader。
  定义 name、capability manifest、create/close 逻辑。

运行实例 (DB + 运行时)
  用户在 Admin UI 中启用某个 Loader 后，创建 IntegrationConfig (DB)。
  factory.create(record, gateway, storage): 创建即激活，返回 IntegrationInstance。
  Integration 通过 gateway.open_integration(integration_id) 拿到 IntegrationIngress。
  instance.close(): 释放资源，IntegrationConfig 保留在 DB。

IntegrationIngress (运行时)
  Integration 收到外部消息后，构造 IncomingMessage 并通过 ingress.emit() 投入 Gateway。
  ingress 自动把 source 盖戳为 MessageSource(producer="integration", id=integration_id, path=...)。
  source.path 由 Integration 自行约定（如 QQ 用 "group:<gid>" / "private:<uid>"）。
```

**Integration 是外部连接的唯一扩展点。** 没有独立于 Integration 的 "Channel" 一等对象——平台层不维护 channels 表。在 Admin UI 中可以按 `(integration_id, source.path)` 把入站消息分组展示成"频道"列表，但这只是 UI 概念，不影响运行时数据流。

每个 capability 至少声明：

```text
id
name
description
input_type    # msgspec.Struct
output_type   # msgspec.Struct
```

Agent 可见面统一由 capability manifest 生成。Actor policy 绑定 capability id（如 `search.query`、`repo.issue_read`），不绑定具体 integration 实例。capability 的 input/output schema 用 `msgspec.json.schema()` 生成，框架内部不引入第二套类型库。

Integration 是真正的扩展点：新增服务主要新增一个 IntegrationFactory + capability manifest，而不是修改多条业务路径。

### Character

Character 是人格和 prompt 模板定义，包含：

- name / description
- system prompt
- facade / tool surface declaration
- builtin base version
- default hints

Prompt Template 是 UI 编辑辅助，不是运行时依赖。Admin 可以把模板内容复制/插入到 Character 的 system prompt 中；Actor Runtime 只读取 Character 保存的完整 `system_prompt`。

Character 不应该强绑定某个 LLM model。它描述"这个角色是谁"，不描述"这个运行实例用什么资源"。

### Actor

Actor 是可被 Gateway 路由到的消息消费终端。

```text
Actor = Character + Model Binding + yuuagents AgentDefinition fields + yuubot Policy
```

示例：

```text
Character: shiori
Actor: shiori-web
  model = openai/gpt-5.2
  memory = enabled
  rollover = enabled

Character: yuu
Actor: yuu-qq-group
  model = deepseek/deepseek-chat
  memory_curator = enabled
  sandbox = restricted
```

### MessageSource

Gateway 路由的唯一标识不是"频道名"，而是 `MessageSource`：

```python
class MessageSource(msgspec.Struct):
    producer: str = "integration"   # "integration" | "system"
    id: str = ""                    # integration_id 或 system:<actor_id>
    path: str = ""                  # integration 自定义的 sub-source 路径
```

每条 `IncomingMessage` 携带一个 `MessageSource`。Integration 通过 `IntegrationIngress.emit()` 投递时，框架自动把 `producer` 设为 `"integration"`、`id` 设为 `integration_id`，`path` 由 Integration 自行填写（语义对 Gateway 透明）。

### ActorIngressRule

平台路由由 `actor_ingress_rules` 表表达。每条规则是 actor + glob 模式：

```python
class ActorIngressRule(msgspec.Struct):
    id: str
    actor_id: str
    source_id_pattern: str = "*"      # fnmatch glob, e.g. "qq-main"
    source_path_pattern: str = "**"   # fnmatch glob, e.g. "group:42*"
    kind_patterns: tuple[str, ...] = ("*",)
    enabled: bool = True
```

启用 actor 自动获得一条隐式 system rule：`source_id == "system:<actor_id>"`，用于 actor 间消息和定时触发。无须管理员手动创建。

```text
IncomingMessage(source, kind) →
  Gateway 遍历 RouteBindings →
  对每条 enabled rule，fnmatch (source.id, source.path, kind) →
  命中则把消息投到 actor mailbox（多 rule 命中按 actor_id 去重）
```

这是 v2 的唯一平台投递模型。不再有独立的 RouteRule、route_rules 表、priority、Context pinning。

### 非平台概念

以下概念**不在 v2 平台模型中**，defer 到实现层（特定 Integration 或 Actor 内部）：

- **Channel 表 / ChannelResource**：平台不持有"频道"一等对象。Admin UI 可以按 `(integration_id, source.path)` 把入站消息分组成"频道"显示，但这是 UI 视图，不对应 DB schema。
- **Context / Session**：会话隔离。QQ Integration 内部如何区分不同群、不同私聊是其实现细节（通常体现在 `source.path`）。GroupActor 可以按 `source.path` 维护 per-group execution state，但平台 Gateway 不持有这个抽象。
- **Thread / Conversation**：同上。平台不知道"线程"——它只匹配 `MessageSource`。
- **Message metadata**：不提供无类型逃生舱。平台特定数据通过 `IncomingMessage` 子类扩展字段表达（如 `QQIncomingMessage.group_id`），Gateway 只看基类。

## Bootstrap Config vs Runtime Resources

v2 的核心分界：

```text
Bootstrap Config
  文件/env。只负责启动系统。通常重启生效。

Runtime Resources
  DB。由 Admin UI 管理。支持在线变更。
```

这能避免过去"配置太多、改动路径不一致、热更新语义不清"的问题。v2 不再让旧 YAML key 形状决定领域模型；旧配置最多作为一次性导入输入，不能成为运行时依赖。

## Integration 生命周期

Integration 的生命周期由三个阶段组成：

```text
1. Loader 注册（代码级）
   IntegrationFactory 在代码中注册到 IntegrationFactoryRegistry。
   这一步不依赖 DB，不依赖外部服务。
   未启用的 Integration 也有对应的 Loader。

2. 运行时启用（DB + 运行时）
   用户在 Admin UI 中启用某个 Integration。
   IntegrationConfig.enabled = True (DB)。
   IntegrationCore.enable(integration_id):
     → factory.create(record, gateway=gateway, storage=storage)：创建即激活
       - Integration 通过 gateway.open_integration(integration_id) 拿到 IntegrationIngress
       - Integration 向外部服务注册（如向 Linear 注册 webhook）
       - Integration 开始接收/发送消息

3. 运行时停用（DB + 运行时）
   用户停用 Integration。
   IntegrationCore.disable(integration_id):
     → instance.close()：释放所有资源
       - Integration 向外部服务注销
       - 不再向 Gateway 投递消息（IntegrationIngress 自然失效）
   IntegrationConfig 保留，enabled=False。
```

Bot 重启时的恢复流程：

```text
Resources.from_store()  # 加载 ResourceRepository / EventBus
  → IntegrationCore.reconcile()
  → 对每个 IntegrationConfig(enabled=True)：factory.create(...)
    → 向外部服务确认/更新注册（factory.create 必须幂等）
  → ActorManager.reconcile() 启动所有 enabled & routed actor
  → Gateway 就绪，开始路由消息
```

## Resources / Repository

`Resources` 是组合根。它持有 `Store`（持久化）+ `EventBus`（进程内事件分发），并构造唯一的 `ResourceRepository`：

```text
Resources(store, secret_codec, event_bus)
  .repository: ResourceRepository  # 所有 CRUD 入口
  .event_bus:  EventBus            # 表级 ResourceChanged 事件
  .close()                         # 关闭 store
```

`ResourceRepository` 提供统一的 `insert / get / list / update / delete` 接口（基于 ORM 类型 + msgspec record），写操作完成后通过 `EventBus` 发布 `ResourceChanged(table, action, row_ids, changed_fields)` 事件。runtime 各组件订阅该事件按需 reconcile（路由刷新、integration 重启、actor 重载）。

写路径统一遵循：`validate references → write DB in transaction → publish ResourceChanged → subscribers refresh`。

## Gateway 架构

Gateway 是单层投递引擎，没有独立的 "domain layer"。

```python
@dataclass
class Gateway:
    routes: RouteBindings                       # ActorIngressRule 快照
    _mailboxes: dict[str, Mailbox]              # actor_id → Mailbox

    def open_integration(integration_id) -> IntegrationIngress
    def get_mailbox(actor_id) -> Mailbox
    def close_mailbox(actor_id) -> None
    def update_bindings(bindings: RouteBindings) -> None
    async def ingest(message: IncomingMessage) -> None
```

`RouteBindings` 是不可变 `tuple[ActorIngressRule, ...]` 快照，由 `load_route_bindings(repository)` 从 `actor_ingress_rules` + 启用 actor 列表构建。daemon 在 `actors` / `actor_ingress_rules` 表变更时调用 `routes.reload() → gateway.update_bindings(new)` 替换快照。

数据流：
```text
ResourceRepository (写 DB)
  → EventBus.publish(ResourceChanged)
    → DaemonRefreshDispatcher
      → RouteBindingService.reload()  → Gateway.update_bindings(new_bindings)
      → ActorManager.reconcile()      → start/stop actors
      → IntegrationCore.reconcile()   → enable/disable integrations
```
