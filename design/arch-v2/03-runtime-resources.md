# 03. Runtime Resources

Runtime Resources 是用户通过 Admin UI 创建、修改、启用、禁用的对象。它们存储在 DB，并支持明确语义下的在线变更。

## Resource 总览

```text
llm_providers
service_providers
secrets
characters
actors
channels
route_rules
contexts
```

## Secrets

Provider 的 API key、OAuth token、refresh token 等都存入 Secret Store。

要求：

- 使用 Bootstrap Config 中的 `secrets.master_key` 加密。
- UI 不回显完整 secret，只显示 masked value。
- secret 更新后 bump provider version。
- 支持 `test connection`。

推荐表：

```sql
CREATE TABLE secrets (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

## LLM Provider

```sql
CREATE TABLE llm_providers (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    provider_type TEXT NOT NULL,
    base_url TEXT,
    api_key_secret_id INTEGER,
    default_model TEXT,
    capabilities JSON NOT NULL,
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

- `provider_type`: `openai`, `anthropic`, `gemini`, `deepseek`, `openai_compatible`, `ollama`, `custom`。
- `capabilities`: `chat`, `vision`, `tool_calling`, `reasoning`, `embedding` 等。
- `models`: UI 可选模型列表，可手填或从 provider 拉取。
- `pricing`: 模型价格和计费信息。
- `budget`: provider 或 actor 级预算限制。

删除规则：

- 如果 Actor 引用该 provider，禁止硬删除，只能 `enabled=false`。
- 禁用后新请求不能选择该 provider；已运行 turn 不打断。

## Service Provider

外部服务也按 provider 管理。

```sql
CREATE TABLE service_providers (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    service_type TEXT NOT NULL,
    auth_type TEXT NOT NULL,
    secret_id INTEGER,
    config JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

示例：

- `web_search:tavily`
- `web_search:exa`
- `github:personal`
- `linear:team`
- `wandb:lab`
- `swanlab:lab`

## Character

Character 是角色模板，不直接绑定模型。

```sql
CREATE TABLE characters (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    sections JSON NOT NULL,
    facade_module TEXT NOT NULL,
    tool_surface JSON NOT NULL,
    default_hints JSON NOT NULL,
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    builtin_version TEXT,
    cloned_from TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

`sections` 示例：

```json
[
  {"type": "file", "path": "shiori/persona.md"},
  {"type": "inline", "content": "你是一个..."},
  {"type": "python_runtime"},
  {"type": "delegates"}
]
```

内置 Character 可以被覆盖，但必须支持 reset 到 builtin version。

## Actor

Actor 是运行实例，是 Gateway 的消息消费终端。

```sql
CREATE TABLE actors (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    character_id INTEGER NOT NULL,
    llm_provider_id INTEGER,
    model TEXT,
    fallback_llm_provider_id INTEGER,
    fallback_model TEXT,
    bot_kinds JSON NOT NULL,
    runtime_policy JSON NOT NULL,
    resource_policy JSON NOT NULL,
    default_private BOOLEAN NOT NULL DEFAULT FALSE,
    default_group BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

`runtime_policy` 示例：

```json
{
  "memory_enabled": true,
  "memory_curator_enabled": true,
  "rollover_enabled": true,
  "summarize_steps_span": 20,
  "max_turns": 50,
  "tool_permissions": ["im", "web", "mem"],
  "sandbox": "restricted"
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

## Channel

Channel 是接入外部消息平台的实例。

```sql
CREATE TABLE channels (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    channel_type TEXT NOT NULL,
    auth_secret_id INTEGER,
    config JSON NOT NULL,
    default_private_actor_id INTEGER,
    default_group_actor_id INTEGER,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'created',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

示例：

```json
{
  "channel_type": "discord",
  "config": {
    "guild_allowlist": ["123"],
    "intents": ["messages", "message_content"]
  },
  "default_private_actor": "shiori-discord",
  "default_group_actor": "yuu-discord"
}
```

## Route Rules

```sql
CREATE TABLE route_rules (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    priority INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    match JSON NOT NULL,
    actor_id INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

示例：

```json
{
  "match": {
    "channel": "discord-main",
    "kind": "thread",
    "metadata.guild_id": "123",
    "metadata.thread_name_contains": "project"
  },
  "actor": "project-manager"
}
```

## Context Binding

Context 应保存稳定身份，并可 pin 到 Actor。

推荐在当前 `contexts` 表上补充：

```text
channel
key
kind
label
metadata
actor_id nullable
last_message_at
archived
```

规则：

- `(channel, key)` 必须唯一。
- 首次消息进入时，如果 `actor_id` 为空，Route Engine 解析 actor，并可写入 `context.actor_id`。
- 后续消息优先使用 `context.actor_id`。
- 管理员可以在 UI 中 reassign context。
