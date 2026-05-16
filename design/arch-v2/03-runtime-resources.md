# 03. Runtime Resources

Runtime Resources 是用户通过 Admin UI 创建、修改、启用、禁用的对象。它们存储在 DB，并支持明确语义下的在线变更。

v2 中 Runtime Resources 是运行时的唯一事实来源。旧 YAML 或临时配置只允许通过一次性 import 写入这些表，不能在运行路径中继续参与解析。

例外：`yuuagents` 的 provider wiring 是 daemon infrastructure，不属于 Runtime Resources。它从 Bootstrap Config 加载，修改后重启 daemon 生效。Runtime Resources 中的 Actor 只保存 `yuuagents.AgentDefinition` 需要的字段和 yuubot policy，不保存或热更新 yuuagents `StageConfig.providers`。

## Resource 总览

```text
llm_backends          -- infra config (hot-updatable, not an integration)
integrations          -- agent capability extensions (IntegrationFactory model)
prompt_templates      -- Admin UI authoring helpers (not a runtime dependency)
characters            -- prompt + facade declaration
actors                -- runnable agent instances (yuuagents-shaped)
actor_ingress_rules   -- glob-based MessageSource → actor_id routing
```

平台**不持有**独立的 `channels` 或 `channel_targets` 表。"频道"在 v2 里是 UI 概念：Admin UI 可以按 `(integration_id, source.path)` 对入站消息分组展示，但运行时只看 `MessageSource` + `ActorIngressRule`。

## LLM Backend

```sql
CREATE TABLE llm_backends (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    yuuagents_provider TEXT NOT NULL,
    provider_options JSON NOT NULL,
    api_key_secret_id INTEGER,
    default_model TEXT,
    default_stream_options JSON NOT NULL,
    model_capabilities JSON NOT NULL,
    models JSON NOT NULL,
    pricing JSON NOT NULL,
    budget JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

字段说明：

- `yuuagents_provider`: 直接对应 `yuuagents.StageConfig.llm.provider`，例如 `openai`、`anthropic`、`openrouter`。
- `provider_options`: 直接传给 yuuagents/yuullm provider constructor，例如 `base_url`、`provider_name`、headers。
- `default_stream_options`: backend/model 默认 stream options。
- `model_capabilities`: 模型能力，例如 `vision`、`tool_calling`、`reasoning`、`embedding`、`structured_output`。
- `models`: UI 可选模型列表，可手填或从 backend 拉取。
- `pricing`: 模型价格和计费信息。
- `budget`: backend 或 actor 级预算限制。

实现要求：

- LLM Backend 是 Actor Runtime 的模型后端 infra 配置，不生成 agent-visible `yb.*` 工具。
- 上层 Actor Runtime 只机械拼装 `StageConfig.llm`，不直接写 `if backend == ...` 的业务分支。
- 新增 LLM 厂商时，主要改动应限制在 yuuagents/yuullm backend wiring 和测试用例。不需要在 yuubot core 中新增 integration。

删除规则：

- 如果 Actor 引用该 backend，禁止硬删除，只能 `enabled=false`。
- 禁用后新请求不能选择该 backend；已运行 turn 不打断。

## Integration

Integration 是 yuubot 与外部世界的连接点。它同时承担协议转换、连接管理和能力暴露三个职责。所有外部连接统一走 Integration 模型，包括 IM 平台（Discord、QQ、Telegram）——它们只是恰好只提供 Channel 的 Integration。

### 三层结构

```text
Loader (代码级)
  注册在 IntegrationFactoryRegistry 中，启动时加载。
  未启用的 Integration 也有对应的 Loader。
  定义 name、capability manifest、create/close 逻辑。

运行实例 (DB 级)
  用户在 Admin UI 中启用某个 Loader 后，创建 IntegrationConfig。
  factory.create(record, gateway, storage): 创建即激活，返回 IntegrationInstance。
  instance.close(): 释放资源，IntegrationConfig 保留在 DB。

Channel (运行时)
  Integration 在启用时向 Gateway 申请 Channel。
  Channel 是消息路由的入口，由 Gateway 管理生命周期。
  IM 类 Integration 的 Channel 就是消息通道。
  Webhook 类 Integration 的 Channel 接收外部事件推送。
```

### DB Schema

```sql
CREATE TABLE integrations (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    config JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

`name` 同时标识 integration kind 与 DB record（例如 `"echo"`、`"qq"`、`"slack"`）；每个 kind 在 DB 中只有一条 record。Integration 需要的内部别名（如给群起显示名）写在 `config` 里，由 factory 自行解释。`config` 是 opaque dict —— 认证信息、endpoint、bot_id 等字段由 factory 自行解释。capabilities 不由 DB 存储，而是由 IntegrationFactory 在代码中声明。这样做的好处是框架完全不需要预先知道每个 Integration 能做什么。

示例：

- `qq:main` 提供消息接入（`IntegrationIngress.emit()` 投递 IncomingMessage）+ 可能的 capability。
- `discord:personal` 提供消息接入 + 可能的 capability。
- `web_search:tavily` 提供 `search.query` capability（不投递消息，纯能力提供者）。
- `github:personal` 提供 `repo.issue_read`、`repo.pr_read` capability + webhook 事件接入。
- `linear:team` 提供 `issue.search`、`issue.update` capability + webhook 事件接入。

Integration 的 capability manifest 由 IntegrationFactory 在代码中声明，不存储在 DB。每个 capability 至少包含 `id`、`name`、`description`、`input_type`、`output_type`（均为 `msgspec.Struct`）。Core 用 `msgspec.json.schema()` 生成 UI 展示用的 JSON Schema，并在 invoke 边界做输入输出 dict 校验；不引入 Pydantic 等第二套类型库。

Actor 的 capability permissions 绑定 capability id，而不是绑定 integration instance。`IntegrationCore` 通过 `IntegrationFactoryRegistry` 和 integration `name` 把 `search.query` 解析到一个启用且健康的 integration instance。

### Config schema（前端表单生成）

`IntegrationORM.config` 是 opaque dict，由 factory 自行解释；但 admin UI 需要一个**静态目录**告诉用户每种 integration kind 有哪些字段、字段含义、默认值等，以便渲染"新建 / 编辑 integration"表单。v2 选择让 factory 自己声明这份目录，而不是在 Core 或 UI 侧维护一张表：

- `IntegrationFactory` 可选声明 `config_schema: type[msgspec.Struct]`，它同时是该 kind 在运行时校验 `record.config` 的 Struct，也是 UI 表单的 schema 源。
- `IntegrationFactory` 可选声明 `description: str`，作为卡片/表单标题下的人类说明。
- Struct 字段用 `typing.Annotated[T, msgspec.Meta(title=..., description=...)]` 携带展示信息，JSON Schema 会原样保留这些字段。
- 敏感字段用 `yuubot.core.secrets.Secret` 标注；schema 输出为 `{type: "string", format: "secret"}`。运行时 integration 必须显式调用 `.reveal()` 才能取到明文，`str()`/`repr()` 始终是 `"***"`。
- 注册中心提供 `IntegrationFactoryRegistry.integration_kinds() -> list[IntegrationKindInfo]`，每一项包含：
  - `name` — 与 `IntegrationORM.name` 匹配；
  - `description` — 可选人类说明；
  - `config_schema` — `msgspec.json.schema()` 生成的 JSON Schema（顶层 `$ref` 已内联为 `{type: "object", ...}`，便于直接 feed 到表单库；嵌套引用才保留 `$defs`）；
  - `capabilities` — 该 kind 暴露的 capability 元信息列表。
- Admin 进程以 `GET /api/integration-kinds` 暴露整份目录（见 `11-api-design.md`）。该目录是进程内静态的：factory 注册一次，生命周期内不变，前端可放心缓存。
- 未声明 `config_schema` 的 factory 返回空 dict，UI 据此折叠表单；此时 `IntegrationORM.config` 仍可存任意 dict，但由 factory 内部验证。

这样新增 integration kind 时，开发者只需：声明 Struct、给字段挂 `Meta` 标注、把 Struct 赋给 `factory.config_schema` —— 表单自动就能被渲染，不必再改动 admin 前端。

### Config secret 持久化

Secret 不是独立资源表，而是 `IntegrationORM.config` 里的敏感字段：

- 写入 DB 时，repository 边界把 `Secret` 值用 `secrets.master_key` 加密为 `{"$enc": "v1", "ct": "..."}`。
- 读取 DB 时，repository 边界识别该形状并解密回 `Secret` wrapper；factory 和 integration instance 永远只看到 wrapper。
- 普通 GET/list 响应只返回 `"***"`；admin 需要展示明文时调用 reveal 端点。
- 编辑表单里 secret 字段留空表示不修改；填新值才覆盖。

`secrets.master_key` 必须是 32 bytes base64，缺失或格式不正确时进程启动失败；v1 不做在线轮换。

### Runtime storage contract

平台给每个 integration 分配一个独占目录：`<paths.data_dir>/integrations/<integration_id>/`。

- enable/create 时平台保证目录存在，并通过 `IntegrationStorage.data_dir` 注入 factory。
- disable/close 不删除目录，便于临时停用后恢复游标、SQLite、媒体元数据等业务数据。
- delete integration 时先关闭运行实例，再删除整个目录。
- Integration 只拿到 `Gateway` 与 `IntegrationStorage`，不直接接触平台 `ResourceRepository`。

### 生命周期

Integration 生命周期分为实例级和运行时级两个层面：

**实例级（factory）：**
- `factory.create(record, *, gateway, storage)` — 创建即激活，返回 IntegrationInstance。Instance 通过 `gateway.open_integration(integration_id)` 拿到 `IntegrationIngress`，并自行管理外部资源（连接、worker、私有数据目录等）。
- `instance.close()` — 释放所有资源（连接、worker）。关闭后 instance 被丢弃，但 IntegrationConfig 保留在 DB 以备重新创建。

**运行时级（IntegrationCore）：**
- `core.enable(integration_id)` — 调用 factory.create() 创建 instance 并注册。
- `core.disable(integration_id)` — 调用 instance.close() 并移除注册。
- `core.reconcile(event=None)` — 比对 DB 中 enabled 状态与运行时实例集合，启停差异。

```text
启用流程：
  用户在 Admin UI 中启用 Integration
  → IntegrationConfig.enabled = True (DB)
  → IntegrationCore.enable(integration_id)
    → factory.create(record, gateway=gateway, storage=storage)
      → instance 通过 gateway.open_integration() 获得 IntegrationIngress
      → instance 向外部服务注册（如向 Linear 注册 webhook）
    → instance 开始接收/发送消息

停用流程：
  用户停用 Integration
  → IntegrationCore.disable(integration_id)
    → instance.close()
      → 向外部服务注销
      → 不再向 Gateway 投递消息（IntegrationIngress 自然失效）
  → IntegrationConfig.enabled = False (DB)

删除流程：
  用户删除 Integration
  → 如果还在启用状态，先 disable()
  → 删除 IntegrationConfig (DB)
  → 删除 <paths.data_dir>/integrations/<integration_id>/
  → 引用该 integration 的 ActorIngressRule（按 source_id_pattern 命中 integration_id）失效，需管理员清理
```

Bot 重启时，`IntegrationCore.reconcile()` 会对每个 `enabled=True` 的 IntegrationConfig 重新调用 `enable()`。`factory.create()` 必须是幂等的——如果外部 webhook 已注册，确认/更新即可。

## Prompt Template

Prompt Template 是 Admin UI 的编辑辅助：用户可以把模板内容复制/插入到 Character 的 system prompt 中。它不是运行时依赖；删除或修改模板不会影响已有 Character。

```sql
CREATE TABLE prompt_templates (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    builtin_version TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

## Character

Character 是角色模板，不直接绑定模型。它保存完整的 system prompt 纯文本；Admin UI 展示的内容就是运行时会传给 yuuagents 的内容。

```sql
CREATE TABLE characters (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    system_prompt TEXT NOT NULL,
    default_prompt_providers JSON NOT NULL,
    facade_module TEXT NOT NULL,
    default_hints JSON NOT NULL,
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    builtin_version TEXT,
    cloned_from TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

内置 Character 可以被覆盖，但必须支持 reset 到 builtin version。

## Actor

Actor 是运行实例，是 Gateway 的消息消费终端。

Actor 的持久化形状应贴近 `yuuagents.AgentDefinition`，避免“yuubot Actor DSL -> Binding -> AgentDefinition”的二次抽象。它可以保留 yuubot 自己的 policy，但执行所需的 capabilities / prompt provider config / budget / LLM options 应直接对应 yuuagents 字段。

```sql
CREATE TABLE actors (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    character_id INTEGER NOT NULL,
    llm_backend_id INTEGER NOT NULL,
    model TEXT,
    llm_options JSON NOT NULL,
    budget JSON NOT NULL,
    agent_capabilities JSON NOT NULL,
    agent_prompt_providers JSON NOT NULL,
    allowed_capability_ids JSON NOT NULL,
    runtime_policy JSON NOT NULL,
    resource_policy JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

字段直接对应：

```text
llm_options             -> AgentDefinition.llm.max_tokens / stream_options
budget                  -> AgentDefinition.budget
agent_capabilities      -> AgentDefinition.capabilities
agent_prompt_providers  -> AgentDefinition.prompts.providers
```

示例：

```json
{
  "agent_capabilities": [
    {
      "provider_key": "ipykernel",
      "config": {
        "imports": [{"module": "yuubot_runtime_yb", "alias": "yb"}],
        "expand_functions": ["yb.*"],
        "state": {"sandbox": "restricted"}
      }
    }
  ],
  "agent_prompt_providers": [
    {"provider_key": "ipykernel", "config": {"level": "summary"}}
  ],
  "allowed_capability_ids": ["search.query", "mem.read"]
}
```

`allowed_capability_ids` 是 yuubot dispatcher / `yb.*` facade 的安全边界；`agent_capabilities` 是 yuuagents executor/tool-spec 配置。两者不要合并。

`runtime_policy` 示例：

```json
{
  "memory_enabled": true,
  "memory_curator_enabled": true,
  "rollover_enabled": true,
  "summarize_steps_span": 20,
  "strict_usage_sink": false
}
```

`resource_policy` 示例：

```json
{
  "budget_usd_daily": 5.0,
  "concurrency_limit": 1,
  "bridge_nodes": ["home-pc", "gpu-node-1"],
  "workspace_access": "read_write"
}
```

`runtime_policy` 和 `resource_policy` 虽然存为 JSON，但不是任意 dict。每个 policy 都需要对应的 typed schema、默认值和 validator，避免调用方猜字段含义。

## ActorIngressRule

`actor_ingress_rules` 表是 v2 唯一的平台路由配置。每条规则描述"哪个 actor 接收什么 source 的消息"，用 fnmatch glob 在 `MessageSource(id, path)` + `kind` 上做匹配。

```sql
CREATE TABLE actor_ingress_rules (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    source_id_pattern TEXT NOT NULL DEFAULT '*',
    source_path_pattern TEXT NOT NULL DEFAULT '**',
    kind_patterns JSON NOT NULL DEFAULT '["*"]',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

字段语义：

- `source_id_pattern`：匹配 `MessageSource.id`。Integration 投入的消息 id 通常是 `integration_id`；Actor 自我触发的消息 id 是 `system:<actor_id>`。
- `source_path_pattern`：匹配 `MessageSource.path`。Integration 自定义，如 `group:42`、`private:user-7`、`webhook:linear-team-x`。
- `kind_patterns`：匹配 `IncomingMessage.kind`，如 `private` / `group` / `system`。

示例：

```json
{
  "actor_id": "shiori-web",
  "source_id_pattern": "web-admin",
  "source_path_pattern": "dialog:*",
  "kind_patterns": ["*"]
}
```

```json
{
  "actor_id": "ops",
  "source_id_pattern": "qq-main",
  "source_path_pattern": "group:42",
  "kind_patterns": ["group"]
}
```

每个 enabled actor 自动获得一条隐式 `system:<actor_id>` rule，由 `build_route_bindings(...)` 在加载时注入；管理员无需手动创建。

UI 语义：

```text
Actor: shiori-web
Ingress Rules:
  qq-main / group:42 / [private,group]
  web-admin / dialog:* / [*]
  + add rule
```

复杂路由（如群聊内按 group_id 分流到不同 actor）通过为同一 integration 配置多条不同 `source_path_pattern` 的 rule 实现；不需要在 actor 内部再做一层 dispatch。
