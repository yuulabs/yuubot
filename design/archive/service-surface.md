> **已过时**：这是历史 ASGI 设计，仅供追溯，不得作为当前实现依据。当前权威设计见
> [`design/system-design.md`](../system-design.md)。

# Design: yuubot ASGI Service Surface

## Problem / Goal

这篇文章描述 yuubot 后端理应暴露给前端和外部 Integration 的 ASGI 服务面。

服务面不是数据库表映射。它只是把当前核心对象 `Yuubot`、`Runtime`、`Actor`、
`Conversation`、`History`、`Integration` 的能力，用 HTTP 和 WebSocket 暴露出来。

目标是：

- HTTP 负责配置、快照、持久化历史、文件读写等请求/响应操作。
- WebSocket 负责对话流、打断、Runtime 事件、Task 输出、history 订阅等长连接操作。
- 传输层使用 yuubot 原生数据结构，尤其是 `HistoryItem` 和 `StreamEvent`。
- 前端按该服务面适配，直接消费 yuubot 的配置、History 和 Stream 协议。

当前 `src/yuubot/web/` 包提供最小 demo ASGI app。正式服务面应收敛到该包内的 contract
route；legacy demo path（`/config`、`/events`、`/llms`、`/actors`、`/integrations`、
`/interrupt` 等）不属于正式 contract，仅供本地调试。

## Concept Map

`ASGIApp` 是 yuubot 的网络入口。它持有一个 `Yuubot` 实例，并把 HTTP / WebSocket 请求翻译
成 `Yuubot` 方法调用。

`Yuubot` 是应用服务层。它管理 LLM config、Integration state、Actor record，并提供
`chat_stream(actor_id, text, conversation_id)` 与 `interrupt(conversation_id)`。

`Runtime` 管理系统资源：HistoryStore、ConversationManager、Task、Mailbox、EventBus、
Gateway、Integration 实例、Cache。

`Actor` 是用户选择的 agent 实体。HTTP 配置 Actor；WebSocket 对话时按 `actor_id` 找到
Actor，并由它创建或复用 Conversation。

`Conversation` 是流式对话执行单元。它产出 yuubot 标准 `StreamEvent`，并把最终
`HistoryItem` 写入 `HistoryStore`。

`Integration` 是代码中已注册的服务连接能力。用户通过 HTTP 填写配置并 enable / disable；
如果需要外部平台推送，则在 ASGI app 上注册自己的 HTTP / WebSocket route，或通过统一
inbound endpoint 投递 `IncomingMessage`。

## Transport Split

HTTP 是无状态请求/响应接口：

- 获取启动所需的配置快照。
- 保存 LLM / Actor 配置，以及 Integration state。
- 查询 Conversation 列表和 History。
- 上传用户文件、下载或浏览 Actor workspace 文件。
- 查询 Task、Runtime、Integration 的当前快照。

WebSocket 是有状态流接口：

- 用户发送消息并接收 `StreamEvent`。
- 用户打断正在运行的 Conversation。
- 前端订阅 Runtime eventbus、Task stdout、conversation history append。
- Integration realtime 不走通用 `/api/ws`；见 Integration Surface 的 dedicated route 或
  `runtime.events.subscribe`。

SSE 可以保留为 demo 或调试入口，但不是正式 contract。正式前端只需要 HTTP + WebSocket。

## HTTP Shape

HTTP response 统一使用 JSON。成功时返回资源本身或一个小对象；失败时返回：

```json
{
  "error": {
    "code": "bad_request",
    "message": "conversation_id is required"
  }
}
```

HTTP 不承载 LLM token stream。需要流式响应时，客户端打开 WebSocket。

常用 HTTP status 与 error code：

| Status | code | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | 请求 JSON、query 或 path 参数不合法。 |
| 401 | `unauthorized` | 管理或 inbound 请求缺少认证信号。 |
| 404 | `not_found` | 指定资源不存在。 |
| 409 | `conflict` | 资源状态冲突：当前 durable 或 runtime 状态不允许该 mutation。 |
| 422 | `configuration_required` | Integration 或 Actor 所需配置缺失。 |
| 500 | `internal_error` | yuubot 内部错误。 |
| 503 | `provider_unavailable` | 外部 provider、Integration 或运行态依赖不可用。 |

`conversation_busy` 只出现在 WebSocket `error` frame，不是 HTTP status code。

各 HTTP endpoint 常见 error code 映射：

| Endpoint 组 | 400 | 401 | 404 | 409 | 422 | 500 | 503 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Bootstrap / healthz | — | — | — | — | — | ✓ | — |
| Config: LLM / Actor / Integration | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Gateway routes CRUD | ✓ | — | ✓ | ✓ | ✓ | ✓ | — |
| Conversation query / delete | ✓ | — | ✓ | ✓ | — | ✓ | — |
| File upload / browse / download | ✓ | — | ✓ | — | — | ✓ | — |
| Admin interrupt / shutdown | ✓ | ✓ | — | — | — | ✓ | — |
| Runtime / Task snapshot | ✓ | — | ✓ | ✓ | — | ✓ | — |
| Inbound integration | ✓ | ✓ | ✓ | — | — | ✓ | ✓ |

✓ 表示该 endpoint 组在对应场景下可能返回该 status。具体触发条件见各 section 的 Error
protocol。

## Bootstrap

```http
GET /healthz
GET /api/bootstrap
```

`/healthz` 只表示 ASGI app 已启动：

```json
{
  "status": "ok"
}
```

`/api/bootstrap` 返回前端首屏需要的全部快照：

```json
{
  "schema_version": 1,
  "deployment": {
    "data_dir": "...",
    "server": { "host": "127.0.0.1", "port": 8765 }
  },
  "llms": [
    {
      "id": "deepseek",
      "provider": "openai_compatible",
      "model": "deepseek-chat",
      "configured": true,
      "last_error": null
    }
  ],
  "actors": [
    {
      "id": "amy",
      "name": "Amy",
      "description": "",
      "enabled": true,
      "status": "running",
      "workspace": ".../workspace/amy",
      "model": "deepseek-chat",
      "tools": ["read", "write", "execute_python"]
    }
  ],
  "integrations": [
    {
      "type": "github",
      "name": "gh",
      "package_path": "yext.github",
      "enabled": false,
      "configured": false,
      "config_schema": {}
    }
  ],
  "conversations": [
    {
      "id": "c1",
      "actor_id": "amy",
      "status": "active",
      "created_at": "2026-07-02T10:00:00+00:00",
      "last_active_at": "2026-07-02T10:05:00+00:00",
      "message_count": 12,
      "last_seq": 11,
      "last_error": null
    }
  ]
}
```

`conversations` 合并 `ConversationRecord` 与 `HistoryStore` 推导字段：`message_count`、
`last_seq` 来自 history append log；其余字段来自 durable conversation record。响应可包含
`schema_version`（来自 `ApplicationState`），供前端判断 durable schema 兼容性。

这是目标 frontend bootstrap view。正式 ASGI app 应把代码 registry 与数据库 state 合并后返回。
字段可以随前端需要扩展，但不应暴露内部数据库表形状。`DeploymentConfig` 是进程启动输入；
LLM、Actor、Integration、Route、Conversation、History 和 Cost 属于 `ApplicationState`。
Bootstrap 可以返回启动相关的只读摘要，但配置 HTTP 不修改 `config.yaml`。

### Facade: `GET /api/bootstrap`

```text
Entrypoint: GET /api/bootstrap
Input protocol: 无 body；无 query。
Context collection: 读取 DeploymentConfig、ApplicationState（llms / actors / integrations /
  routes / conversations）、registry spec、runtime status（enabled / status / last_error）、
  HistoryStore conversation 摘要。
Core call: bootstrap_snapshot(app) — 合并 durable record 与 runtime 视图。
Output protocol: 200 JSON，shape 见上；含 deployment、llms、actors、integrations、
  conversations；可选 schema_version。
Error protocol: 500 internal_error（数据库或 runtime 读取失败）。
Persistence: 只读；不写数据库或 config.yaml。
Compatibility: 可追加顶层或嵌套字段；不删除或重命名已有字段。schema_version 递增表示
  durable schema 不兼容变更。
```

## Config HTTP

配置接口分两类：LLM / Actor 是用户维护的业务配置；Integration 是代码 registry 中已经存在
的系统能力，用户只能保存 state、enable、disable。每个 Integration `type` 在 yuubot 中是
一个系统级能力实例；多账号、多工作区或多个远端身份属于该 Integration 自己的内部配置，
yuubot 不为同一 `type` 创建多个 Integration 实例。

```http
PUT /api/llms/{llm_id}
DELETE /api/llms/{llm_id}

PUT /api/actors/{actor_id}
POST /api/actors/{actor_id}/enable
POST /api/actors/{actor_id}/disable
DELETE /api/actors/{actor_id}

GET  /api/integrations
GET  /api/integrations/{type}
PUT  /api/integrations/{type}/config
POST /api/integrations/{type}/enable
POST /api/integrations/{type}/disable
```

`PUT /api/llms/{llm_id}` 的 body 是 `LLMInput`，由 facade 转成 durable `LLMRecord` 并映射
到运行时 `LLMClientConfig`：

| LLMInput 字段 | LLMRecord / LLMClientConfig |
| --- | --- |
| `provider` | `provider` |
| `model` | `model` |
| `endpoint` | `endpoint` |
| `api_key_ref` | `api_key_ref` |
| `options` | `options` |

```json
{
  "provider": "openai_compatible",
  "model": "deepseek-chat",
  "endpoint": "https://api.deepseek.com",
  "api_key_ref": "DEEPSEEK_API_KEY",
  "options": {}
}
```

`PUT /api/actors/{actor_id}` 的 body 是 `ActorInput`，由 facade 转成 durable `ActorRecord`。
Actor input 拥有 persona、模型选择、workspace 和 tools；没有单独的 capability set 表。PUT
同时 upsert record 并 enable actor（启动 task）。`POST .../enable` 从已有 record 重新
enable；`POST .../disable` 停止 actor task 并关闭其 conversation，但保留 durable record；
`DELETE` 执行 `remove_actor`：先 disable，uninstall tools，再丢弃 record。

| ActorInput 字段 | ActorRecord / ActorConfig |
| --- | --- |
| `id` | `id` |
| `name` | `name` |
| `description` | `description` |
| `workspace` | `config.workspace` |
| `persona` | `config.persona` |
| `llm` | `config.llm`（LLM id） |
| `model` | `config.model`（`ModelCard`） |
| `tools` | `config.tools`（`dict[str, ToolConfig]`） |

```json
{
  "id": "amy",
  "name": "Amy",
  "description": "",
  "workspace": "",
  "persona": "",
  "llm": "deepseek",
  "model": {
    "selector": "deepseek-chat",
    "toolcall": true
  },
  "tools": {
    "read": { "type": "read" },
    "write": { "type": "write" }
  }
}
```

Integration 列表来自 `Runtime.integration_registry` 与数据库 state 的合并结果。用户没有
“创建 Integration type”的操作。

`GET /api/integrations` 返回 registry + DB state 合并视图：

```json
{
  "items": [
    {
      "type": "github",
      "name": "gh",
      "title": "GitHub",
      "package_path": "yext.github",
      "enabled": false,
      "configured": false,
      "last_error": null,
      "config_schema": {},
      "secret_fields": ["access_token"]
    }
  ]
}
```

`title` 与 `secret_fields` 来自 Integration registry spec，供前端渲染卡片与 secret 表单。
`GET /api/integrations/{type}` 返回上述单条 item；未知 type 返回 404。

`PUT /api/integrations/{type}/config` 保存某个内置 Integration 的本地配置。`secret_refs`
写入 durable `IntegrationRecord`；若该 Integration 已 enabled，facade 在持久化后 hot-reload
运行时实例，使新 config / secret ref 立即生效：

```json
{
  "name": "gh",
  "config": {
    "default_owner": "yuulabs",
    "default_repo": "llm-bot",
    "base_url": "https://api.github.com"
  },
  "secret_refs": {
    "access_token": "GITHUB_TOKEN"
  }
}
```

`POST /api/integrations/{type}/enable` 从 registry spec 与数据库 state 派生运行时
`IntegrationRecord`，再调用 `runtime.enable_integration(record)`。如果必填配置缺失，返回
`configuration_required`（422），前端打开该 Integration 的 schema 表单。

`POST /api/integrations/{type}/disable` 停止运行时 Integration，但保留数据库里的配置。

HTTP 配置 API 写数据库，不写 `config.yaml`。`config.yaml` 只保存启动程序必要的只读配置。
写入成功后返回新的 bootstrap snapshot。这样前端无需猜测局部更新是否改变了 schema、
workspace、Integration context 或 Actor 可用 tools。

配置 mutation 常见 error code：

- `422 configuration_required`：Integration enable 或 Actor PUT 时必填字段、LLM 引用或 tool
  config 缺失。
- `409 conflict`：DELETE actor 时 actor task 仍在运行且无法安全停止；DELETE llm 时仍有 actor
  引用；DELETE integration route 时 gateway 正在 dispatch 相关 route（实现可串行化后放宽）。
- `503 provider_unavailable`：LLM probe 或 Integration 构造/连接失败（enable 或 config
  hot-reload 时）。

### Facade: `DELETE /api/llms/{llm_id}`

```text
Entrypoint: DELETE /api/llms/{llm_id}
Input protocol: path llm_id；无 body。
Context collection: ApplicationState.llms；引用该 llm 的 ActorRecord 列表。
Core call: 若仍有 actor 引用则拒绝；否则 discard LLMRecord 与运行时 client cache。
Output protocol: 200 bootstrap snapshot。
Error protocol: 404 not_found；409 conflict（actor 仍引用该 llm）；500 internal_error。
Persistence: 删除 durable LLMRecord；不写 config.yaml。
Compatibility: 删除后 bootstrap.llms 不再包含该 id；历史 conversation 不受影响。
```

### Facade: Actor enable / disable / remove

```text
Entrypoint: POST /api/actors/{actor_id}/enable
           POST /api/actors/{actor_id}/disable
           DELETE /api/actors/{actor_id}
Input protocol: path actor_id；enable/disable 无 body。
Context collection: ActorRecord；runtime.actors；ConversationManager。
Core call: enable → construct_from_record + create_task(actor.run)；disable → cancel_tasks +
  actor.close + close_for_actor；DELETE → remove_actor（disable + tool uninstall + discard record）。
Output protocol: 200 bootstrap snapshot。
Error protocol: 404 not_found；409 conflict（DELETE 时 task 无法安全停止）；422
  configuration_required（enable 时 record 缺 llm/model/tools）；500 internal_error。
Persistence: enable/disable 更新 ActorRecord.status 与 last_error；DELETE 丢弃 record 与
  workspace 级 tool 安装资产（History 按产品策略保留）。
Compatibility: disable 后 durable record 仍存在，bootstrap 中 enabled=false。
```

### Facade: Integration list / get

```text
Entrypoint: GET /api/integrations
           GET /api/integrations/{type}
Input protocol: path type（单条）；无 body。
Context collection: integration_registry.specs()；ApplicationState integration records 与
  runtime.integration_statuses()。
Core call: merge registry spec + DB state + runtime enabled 标志。
Output protocol: list 200 { items: [...] }；get 200 单条 item（见上 JSON）。
Error protocol: 404 not_found（未知 type）；500 internal_error。
Persistence: 只读。
Compatibility: items 可追加 last_error、config_schema 字段；secret_fields 只列名不返回值。
```

### Facade: `PUT /api/llms/{llm_id}`

```text
Entrypoint: PUT /api/llms/{llm_id}
Input protocol: JSON body = LLMInput（provider, model, endpoint?, api_key_ref?, options）。
Context collection: path llm_id；SecretPolicy 解析 api_key_ref（不返回 secret 值）。
Core call: persist LLMRecord；更新 LLMClientConfig cache；可选 probe provider。
Output protocol: 200 JSON = 完整 bootstrap snapshot。
Error protocol: 400 bad_request（body 非法）；503 provider_unavailable（probe 失败）；
  500 internal_error。
Persistence: app_llms 表；不写 config.yaml。
Compatibility: LLMInput 可增 options 子字段；id 由 path 决定，body 不含 id。
```

### Facade: `PUT /api/actors/{actor_id}`

```text
Entrypoint: PUT /api/actors/{actor_id}
Input protocol: JSON body = ActorInput；path actor_id 覆盖 body.id。
Context collection: registry 解析 tools；校验 llm id 存在；workspace 目录策略。
Core call: upsert ActorRecord → enable_actor（construct、attach mailbox、start task）。
Output protocol: 200 JSON = bootstrap snapshot。
Error protocol: 400 bad_request；422 configuration_required（unknown llm、tool schema）；
  503 provider_unavailable（LLM 不可用）；500 internal_error。
Persistence: app_actors 表；workspace 目录按 ActorConfig 创建。
Compatibility: tools dict 键为 tool id；新增 tool type 由 registry 扩展。
```

### Facade: Integration config / enable / disable

```text
Entrypoint: PUT /api/integrations/{type}/config
           POST /api/integrations/{type}/enable
           POST /api/integrations/{type}/disable
Input protocol: PUT body = { name, config, secret_refs? }；enable/disable 无 body。
Context collection: integration_registry.specs()[type]；当前 IntegrationRecord；
  enabled 运行时实例（config PUT 时）。
Core call: configure_integration / enable_integration / disable_integration。
Output protocol: 200 JSON = bootstrap snapshot。
Error protocol: 404 not_found（未知 type 或未 configured 就 disable）；400 bad_request；
  422 configuration_required（enable 缺配置）；503 provider_unavailable（构造失败）；
  500 internal_error。
Persistence: app_integrations 表；secret_refs 存 ref 名，不存明文。
Compatibility: config / secret_refs 键由 Integration spec 的 config_schema 约束。
```

## Gateway Routes HTTP

Gateway route 把 Integration 入站消息映射到 Actor mailbox。Route 属于 `ApplicationState`，
由 HTTP CRUD 管理；mutation 后 gateway 立即 rebind 内存索引。

```http
GET    /api/routes
POST   /api/routes
PUT    /api/routes/{route_id}
DELETE /api/routes/{route_id}
```

Route JSON shape 对齐 `RouteRecord`：

```json
{
  "id": "qq-group-232411",
  "integration_id": "qq",
  "pattern": "qq.group.id:232411",
  "actor_id": "amy",
  "enabled": true
}
```

`integration_id` 是 Integration type（与 `IntegrationRecord.type` 一致）。`pattern` 使用
`<integration_qualifier>:<match>` 格式，由 gateway 与 `IncomingMessage.route` 精确或通配匹配。
示例（来自 design.md）：

```text
qq.group.id:232411        -> actor amy
qq.group.name:*acg*       -> actor amy（群名含 acg 的通配）
```

`POST /api/routes` 创建 route；`PUT` upsert；`DELETE` 删除 record 并 rebind。`enabled: false`
保留 record 但不参与 dispatch。

### Facade: Gateway routes CRUD

```text
Entrypoint: GET /api/routes
           POST /api/routes
           PUT /api/routes/{route_id}
           DELETE /api/routes/{route_id}
Input protocol: GET 无 body。POST/PUT JSON = RouteRecord 字段（id 可由服务端生成或 path 指定）。
Context collection: integration_registry 校验 integration_id；actors 校验 actor_id 存在。
Core call: persist RouteRecord → runtime.gateway.rebind(routes)。
Output protocol: GET 200 { "items": [RouteRecord, ...] }；mutation 200 返回更新后的 route 或
  items 列表（与项目其它 list endpoint 一致）。
Error protocol: 400 bad_request（pattern 非法）；404 not_found（route_id / actor_id 不存在）；
  409 conflict（actor 被删除中、route_id 重复）；422 configuration_required（integration 未
  enable）；500 internal_error。
Persistence: integration_routes 表（durable ApplicationState）。
Compatibility: pattern 语法可扩展；新增字段不影响旧客户端。integration_id 重命名视为不兼容变更。
```

## Conversation HTTP

HTTP 负责查询持久化状态，不负责运行对话。

```http
GET /api/conversations
GET /api/conversations/{conversation_id}
GET /api/conversations/{conversation_id}/history
GET /api/conversations/{conversation_id}/costs
DELETE /api/conversations/{conversation_id}
```

`GET /api/conversations` 返回 `ConversationRecord` 与 history 推导字段合并后的 summary list：

```json
{
  "items": [
    {
      "id": "c1",
      "actor_id": "amy",
      "status": "active",
      "created_at": "2026-07-02T10:00:00+00:00",
      "last_active_at": "2026-07-02T10:05:00+00:00",
      "message_count": 12,
      "last_seq": 11,
      "last_error": null
    }
  ]
}
```

`status` 取值：`active` | `blocked` | `interrupted` | `closed`（见 lifecycle
`ConversationRecord`）。`message_count` / `last_seq` 来自 `HistoryStore` append log。

`GET /api/conversations/{conversation_id}` 返回单条 conversation 详情：上述
`ConversationRecord` 字段 + `message_count` / `last_seq` + 运行态辅助字段：

```json
{
  "id": "c1",
  "actor_id": "amy",
  "status": "active",
  "created_at": "2026-07-02T10:00:00+00:00",
  "last_active_at": "2026-07-02T10:05:00+00:00",
  "message_count": 12,
  "last_seq": 11,
  "last_error": null,
  "active": true,
  "history_url": "/api/conversations/c1/history"
}
```

`active` 表示 runtime 中仍有 live `Conversation` 对象（可能与 `status` 不完全同步，以实现为准）。

`GET /api/conversations/{conversation_id}/history` 返回带序号和 kind 的 history wrapper。
`payload` 是 yuubot 原生 `HistoryItem` 成员。响应**不包含**会话创建时写入的前缀
`tool_specs` / `system_prompt`；完整 History 仅存在于 `HistoryStore` 供续聊与 LLM 请求
还原。`message_count` 与 list summary 同样按交互段计数。

```json
{
  "conversation_id": "c1",
  "items": [
    {
      "seq": 0,
      "kind": "input",
      "payload": {
        "role": "user",
        "name": "amy",
        "content": [
          { "kind": "text", "text": "hello", "mime": "text/plain" }
        ]
      },
      "created_at": "..."
    },
    {
      "seq": 1,
      "kind": "gen_text",
      "payload": {
        "text": "hi"
      },
      "created_at": "..."
    }
  ]
}
```

`GET /api/conversations/{conversation_id}/costs` 返回 append-only `CostRecord` list：

```json
{
  "conversation_id": "c1",
  "items": [
    {
      "conversation_id": "c1",
      "seq": 0,
      "usage": {
        "input_tokens": 100,
        "output_tokens": 20
      },
      "account": {},
      "estimated": true,
      "created_at": "..."
    }
  ]
}
```

`DELETE /api/conversations/{conversation_id}` 依次：discard runtime `Conversation` 对象；
删除 history append log；删除 durable `ConversationRecord` 与关联 `CostRecord`。全部成功或
部分存在时返回 `{ "id": "...", "deleted": true }`；无任何记录时 404。

`kind` 与对外暴露的 `HistoryItem` union 成员对应（不含前缀 `tool_specs` /
`system_prompt`）：

```text
InputMessage
GenText
GenReasoning
GenToolCall
GenImage
GenAudio
ToolResult
```

前端直接渲染这个 shape。后端不需要再为对话历史维护另一套展示格式。

### Facade: Conversation list / get / history / costs / delete

```text
Entrypoint: GET /api/conversations
           GET /api/conversations/{conversation_id}
           GET /api/conversations/{conversation_id}/history
           GET /api/conversations/{conversation_id}/costs
           DELETE /api/conversations/{conversation_id}
Input protocol: path conversation_id；无 body。
Context collection: ApplicationState conversations + costs；HistoryStore；runtime
  ConversationManager（active 标志、discard）。
Core call: list_conversations / conversation_summary / load_wrapped / load_costs /
  discard + history.delete + state.delete_conversation。
Output protocol: 200 JSON，shape 见上。list 包装为 { "items": [...] }。
Error protocol: 404 not_found（conversation 不存在）；409 conflict（DELETE 时 conversation
  正在 run_loop 且无法安全 discard）；500 internal_error。
Persistence: DELETE 写 app_conversations、app_costs、history append log；其它只读。
Compatibility: history payload 随 HistoryItem union 扩展；costs items 只追加 seq，不修改历史行。
```

### Implementation notes

`GET .../costs` 与完整 DELETE（含 costs 清理）在目标 contract 中定义；实现以
`ApplicationState.load_costs` / `delete_conversation` 为准对接。

## File HTTP

文件接口服务 Actor workspace。所有 path 都必须做 containment check，不能越过
`ActorConfig.workspace`。文件 path 使用 workspace-relative path；Facade 负责规范化、
拒绝 `..`、绝对路径和符号链接逃逸。

```http
POST /api/actors/{actor_id}/uploads
GET /api/actors/{actor_id}/files/{path}
GET /api/actors/{actor_id}/browse?path=...
```

上传使用 `multipart/form-data`。文件落在 Actor workspace 的 `uploads/<mime-category>/`
下，其中 `<mime-category>` 由 MIME type 规范化得到（如 `application/pdf` →
`application-pdf`）。重名时 facade 在同目录生成不冲突文件名；返回值使用 `ContentItem`
可引用的信息：

```json
{
  "files": [
    {
      "kind": "file",
      "path": "uploads/application-pdf/report.pdf",
      "mime": "application/pdf",
      "meta": {
        "name": "report.pdf",
        "size": 12345
      }
    }
  ]
}
```

下载文件返回原始 bytes，并设置 `content-type` 与 `content-length`。`browse` 返回目录快照：

```json
{
  "path": "uploads/application-pdf",
  "entries": [
    {
      "name": "report.pdf",
      "path": "uploads/application-pdf/report.pdf",
      "kind": "file",
      "size": 12345,
      "mtime": "...",
      "mime": "application/pdf"
    }
  ]
}
```

发送消息时，WebSocket command 可以把这些文件作为 `content` 传回后端。当前
`Conversation.run_loop(text)` 只接收纯文本；正式服务面应把它扩展为 `list[ContentItem]`，
或者先在 gateway 层把文本和文件引用合成为用户输入。

### Facade: File upload / browse / download

```text
Entrypoint: POST /api/actors/{actor_id}/uploads
           GET /api/actors/{actor_id}/browse?path=...
           GET /api/actors/{actor_id}/files/{path}
Input protocol: POST multipart/form-data（至少一个 file part）；browse query path 默认为
  workspace root；download path 为 workspace-relative。
Context collection: 解析 actor_id → ActorConfig.workspace；containment check（拒绝 ..、绝对路径、
  symlink escape）。
Core call: save_uploads / directory_snapshot / read_bytes。
Output protocol: POST 200 { files: ContentItem[] }；browse 200 directory snapshot；
  download 200 raw bytes + Content-Type + Content-Length。
Error protocol: 404 not_found（actor 或 path 不存在）；400 bad_request（multipart 非法、path
  逃逸）；500 internal_error。
Persistence: 文件写入 actor workspace 磁盘；uploads/<mime-category>/filename。
Compatibility: 新增 meta 字段可追加；path 始终 workspace-relative posix。
```

## WebSocket Shape

正式入口：

```http
GET /api/ws
```

所有 frame 都是 JSON object。客户端命令：

```json
{
  "id": "client-message-id",
  "type": "conversation.send",
  "payload": {}
}
```

服务端响应和 push：

```json
{
  "id": "client-message-id",
  "type": "conversation.stream",
  "payload": {}
}
```

### Connection lifecycle

```text
accept
  -> create WsListener(connection)
  -> runtime.listeners.add(ws_listener)
  -> loop: receive_text -> handle_ws_command
disconnect / close
  -> runtime.listeners.remove(ws_listener)
  -> cancel connection-local tasks
```

每个 WebSocket 连接对应一个 `WsListener`，加入 `Runtime.listeners`（`ListenerHub`）。
客户端发 subscribe / send 命令时，只更新该 `WsListener` 的 filter；**Conversation 与 Task
只 emit eventbus 事件**，由 `WsListener` 决定向这条连接 push 哪些 WS frame。

常驻 listener（`Gateway`、`TaskDeliveryListener` 等）同样在 hub 里，与 `WsListener` 并列。

- `conversation.interrupt` 和 `task.cancel` 在 receive loop 内同步完成。
- 同一连接可并行多个 filter（不同 `conversation_id`、task、`runtime.events` kinds）。
- 连接断开时移除 `WsListener`，不再 push。

### Frame ordering

服务端必须保证以下顺序契约：

| 命令 | ack frame | push frames | 终端条件 |
| --- | --- | --- | --- |
| `conversation.send` | `conversation.send.accepted` | `conversation.stream` × N | 最后一个 `event.kind == "stream_stop"`，或 `error` |
| `conversation.interrupt` | `conversation.interrupt.result` | — | 单帧 result |
| `conversation.history.subscribe` | `conversation.history.subscribe.result` | `conversation.history.append` × N | 连接断开 |
| `runtime.events.subscribe` | `runtime.events.subscribe.result` | `runtime.event` × N | 连接断开 |
| `task.subscribe` | `task.subscribe.result` | `task.event` × N | task 终态后最后一帧 status event，或连接断开 |
| `task.cancel` | `task.cancel.result` | — | 单帧 result |

通用规则：

- `*.accepted` / `*.result` 必须在同 command 的 push stream 之前发出。
- `conversation.send.accepted` 必须在任何 `conversation.stream` 之前。
- 各类 `*.subscribe.result` 必须在对应 push event 之前。
- 纯 push frame 可以省略 `id`；command 关联的 stream 应携带发起 command 的 `id`。

`id` 用于关联一次客户端命令。同一个 `conversation_id` 的 `conversation.send` 应串行执行；
是否允许并发对话由 `ConversationManager` 和 Actor 调度策略决定。

通用错误 frame：

```json
{
  "id": "client-message-id",
  "type": "error",
  "error": {
    "code": "bad_request",
    "message": "actor_id is required",
    "detail": {}
  }
}
```

未知 command type 返回 `bad_request`。未知 server event type 对客户端应是可忽略扩展；客户端
只需要处理自己认识的 `type`。

Integration realtime 不是通用 WS command。平台 adapter 使用 dedicated route
`GET /api/integrations/{type}/ws`，或前端通过 `runtime.events.subscribe` 观察
`incoming.message` / `gateway.dispatch` 等事件。

## Conversation Commands

### StreamEvent wire catalog

WebSocket `conversation.stream` 原样转发 yuubot `StreamEvent`。wire 只使用 delta kinds；
`design.md` 中的 `Gen***Start` / `Gen***End` 是 `merge` 内部概念，不直接出现在 wire 上。

| `event.kind` | `payload` | 说明 |
| --- | --- | --- |
| `text_delta` | `{ "text": "..." }` | 可见回复 token 增量 |
| `reasoning_delta` | `{ "text": "..." }` | reasoning token 增量 |
| `tool_name` | `{ "id": "call-1", "name": "read" }` | tool call 名称确定 |
| `tool_arguments_delta` | `{ "text": "..." }` | tool arguments JSON 增量 |
| `tool_arguments_end` | `{}` | 该 `group_id` 的 arguments 结束 |
| `stream_stop` | 见下 | 本轮 LLM stream 结束 |

同一 `GenText` / `GenReasoning` / `GenToolCall` 的所有 delta 共用同一个 `group_id`。
`merge(chunks)` 按 `group_id` 聚合成 `HistoryItem`。

`stream_stop` 完整 payload：

```json
{
  "reason": "stop",
  "usage": {
    "input_tokens": 100,
    "cached_input_tokens": 20,
    "output_tokens": 30,
    "payg_cost": 0.0001
  },
  "account": {
    "credits": 12.5,
    "quota": 0.8
  }
}
```

- `reason`：`stop` | `length` | `tool_calls` | `content_filter` | `function_call` | `interrupted`
- `usage`：`input_tokens`、`cached_input_tokens`、`output_tokens`、`payg_cost`（可为 `null`）
- `account`：provider 账户快照；可为 `{}`；字段 provider-specific（如 `credits`、`quota`）

### `conversation.send`

客户端 input 接受 `content: list[ContentItem]` 或 legacy `text: string`（等价于单个 text
`ContentItem`）。Facade 将 payload 规范化为 `list[ContentItem]`，再映射为 `InputMessage`
（`role=user`，`name=actor_id`）交给 `Conversation.run_loop`。

客户端：

```json
{
  "id": "m1",
  "type": "conversation.send",
  "payload": {
    "actor_id": "amy",
    "conversation_id": "c1",
    "content": [
      { "kind": "text", "text": "hello", "mime": "text/plain" }
    ]
  }
}
```

Legacy：

```json
{
  "id": "m1",
  "type": "conversation.send",
  "payload": {
    "actor_id": "amy",
    "conversation_id": "c1",
    "text": "hello"
  }
}
```

Facade contract：

```text
_ws_input_content(payload) -> list[ContentItem]
actor = app.actors[actor_id]
conversation = runtime.conversations.get_or_create(actor, conversation_id)
ws_listener.track_send(command_id=id, conversation_id=conversation_id)
asyncio.create_task(conversation.run_loop(content))  # 只 emit；不传入 on_event
ws_listener 将 conversation.stream（匹配 conversation_id）转成 conversation.stream frame
```

服务端接受命令后先返回 ack：

```json
{
  "id": "m1",
  "type": "conversation.send.accepted",
  "payload": {
    "conversation_id": "c1"
  }
}
```

如果同一个 `conversation_id` 已经有一轮 `conversation.send` 在运行，服务端返回：

```json
{
  "id": "m1",
  "type": "error",
  "error": {
    "code": "conversation_busy",
    "message": "conversation is already running"
  }
}
```

服务端每收到一个 yuubot `StreamEvent`，立刻转发 push frame：

```json
{
  "id": "m1",
  "type": "conversation.stream",
  "payload": {
    "conversation_id": "c1",
    "event": {
      "group_id": "text-0",
      "kind": "text_delta",
      "payload": {
        "text": "hi"
      }
    }
  }
}
```

终端条件：`event.kind == "stream_stop"` 表示本轮 LLM stream 结束。最终 history 已由
`Conversation` 写入 `HistoryStore`；前端可相信 stream append view，也可随后用 HTTP
重新读取 history。

错误：

- `ConversationBlocked`（`stop.reason` 为 `length` / `content_filter` 等不可继续 reason）→
  `error` frame，`code = "conversation_blocked"`，`detail.reason` 为 blocked reason。
- 未处理异常 → `error` frame，`code = "internal_error"`。

```json
{
  "id": "m1",
  "type": "error",
  "error": {
    "code": "conversation_blocked",
    "message": "conversation blocked",
    "detail": {
      "reason": "content_filter"
    }
  }
}
```

### `conversation.interrupt`

客户端：

```json
{
  "id": "m2",
  "type": "conversation.interrupt",
  "payload": {
    "conversation_id": "c1"
  }
}
```

Facade contract：

```text
interrupted = app.interrupt(conversation_id)
  -> runtime.conversations.interrupt(conversation_id)
  -> conversation.stop_event.set() when conversation exists
```

幂等：无 active loop 时 `interrupted = false`。

响应 ack：

```json
{
  "id": "m2",
  "type": "conversation.interrupt.result",
  "payload": {
    "conversation_id": "c1",
    "interrupted": true
  }
}
```

如果打断抵达 LLM stream，对应 `conversation.send` 的最后一个 `StreamEvent` 应为：

```json
{
  "group_id": "stop",
  "kind": "stream_stop",
  "payload": {
    "reason": "interrupted"
  }
}
```

### `conversation.history.subscribe`

订阅某个 conversation 的新增 history item。这个命令不是运行对话，只是让前端在多窗口、
Integration inbound、Actor unattended 模式下获得增量更新。

客户端：

```json
{
  "id": "m3",
  "type": "conversation.history.subscribe",
  "payload": {
    "conversation_id": "c1"
  }
}
```

Facade contract：

```text
ack conversation.history.subscribe.result
ws_listener.track_history(conversation_id=payload.conversation_id)
# WsListener 将 conversation.history.append（匹配 id）转成 WS frame
```

ack frame（必须在任何 append push 之前）：

```json
{
  "id": "m3",
  "type": "conversation.history.subscribe.result",
  "payload": {
    "conversation_id": "c1"
  }
}
```

服务端 push `conversation.history.append`；`item` 使用与 HTTP history 相同的 wrapped
`HistoryItem` shape（`seq`、`kind`、`payload`、`created_at`），不是 bare gen payload：

```json
{
  "type": "conversation.history.append",
  "payload": {
    "conversation_id": "c1",
    "item": {
      "seq": 1,
      "kind": "gen_text",
      "payload": {
        "text": "done"
      },
      "created_at": "..."
    }
  }
}
```

终端条件：连接断开，或客户端发送新的 subscribe command 替换旧订阅（实现可选）。
底层 `EventBus` 提供 async pubsub；`lifecycle.md` 中 runtime event 是观测与业务事件的统一出口。

## Runtime Commands

Runtime 层有两类需要长连接暴露的东西：eventbus 和 tasks。

HTTP 提供 snapshot 和控制入口：

```http
GET  /api/runtime
GET  /api/tasks
GET  /api/tasks/{task_id}
POST /api/tasks/{task_id}/cancel
```

HTTP 提供运行态快照与 task 控制；WebSocket 提供增量订阅。快照 shape 来自
`snapshots.runtime_snapshot` / `snapshots.task_snapshot`。

`GET /api/runtime` 返回 `runtime_snapshot`：

```json
{
  "data_dir": "/data/yuubot",
  "workspace_dir": "/data/yuubot/workspace",
  "tasks": [
    {
      "id": "actor:amy",
      "status": "running",
      "error": null,
      "result": null,
      "stdout": "booting actor\n"
    }
  ],
  "actors": [
    {
      "id": "amy",
      "status": "running",
      "mailbox": "actor:amy"
    }
  ],
  "integrations": [
    {
      "name": "gh",
      "package_path": "yext.github"
    }
  ],
  "events": [
    {
      "time": 1719907200.5,
      "kind": "gateway.dispatch",
      "payload": {
        "route": "qq.group.id:232411",
        "actor_id": "amy",
        "delivered": true,
        "conversation_id": "route-c1"
      }
    }
  ]
}
```

`events` 为 eventbus 最近条目（当前实现保留最近 100 条），供运维面板与调试使用。

`GET /api/tasks` 返回 task 列表：

```json
{
  "items": [
    {
      "id": "actor:amy",
      "status": "running",
      "error": null,
      "result": null,
      "stdout": ""
    }
  ]
}
```

`GET /api/tasks/{task_id}` 返回单条 `task_snapshot`。`POST /api/tasks/{task_id}/cancel` 调用
`runtime.cancel_task(task_id)` 后返回 cancel 完成后的 `task_snapshot`。

`task_snapshot` 字段：

| 字段 | 说明 |
| --- | --- |
| `id` | task id，例如 `actor:amy` |
| `status` | `pending` \| `running` \| `done` \| `cancelled` \| `failed`（内部 `error` 状态映射为 `failed`） |
| `error` | 终态错误消息；无错误为 `null` |
| `result` | task 返回值；任意 JSON |
| `stdout` | 当前已缓冲的全部 stdout 拼接 |

### Facade: Runtime / Task HTTP snapshot / cancel

```text
Entrypoint: GET /api/runtime
           GET /api/tasks
           GET /api/tasks/{task_id}
           POST /api/tasks/{task_id}/cancel
Input protocol: GET 无 body；POST cancel 无 body。
Context collection: runtime.tasks、runtime.actors、runtime.integrations、eventbus.events（runtime
  snapshot）；path task_id 定位单 task。
Core call: runtime_snapshot(app) / task_snapshot(app, task_id) / runtime.cancel_task(task_id)。
Output protocol: GET /api/runtime 200 runtime_snapshot；GET /api/tasks 200 { items: task_snapshot[] }；
  GET /api/tasks/{task_id} 200 task_snapshot；POST cancel 200 task_snapshot（终态或 cancelling 后快照）。
Error protocol: 404 not_found（未知 task_id）；400 bad_request；409 conflict（task 已终态且实现拒绝重复
  cancel）；500 internal_error。
Persistence: snapshot 只读；cancel 不写 durable ApplicationState，仅影响运行中 task。
Compatibility: runtime_snapshot 可追加顶层字段；task_snapshot 可追加字段；status 枚举扩展须保持 wire
  映射文档。
```

### Implementation notes

`events` 缓冲长度以实现为准；contract 保证字段存在，不保证固定条数。非 loopback 部署时 management
HTTP 须认证（见 Auth And Secrets）；当前实现尚未对 runtime/task HTTP 强制认证。

### `runtime.events.subscribe`

客户端：

```json
{
  "id": "r1",
  "type": "runtime.events.subscribe",
  "payload": {
    "kinds": ["conversation.input", "conversation.output", "conversation.cost", "gateway.dispatch"]
  }
}
```

`kinds` 为空列表表示订阅全部 kind。

Facade contract：

```text
ack runtime.events.subscribe.result with sorted kinds
ws_listener.track_events(kinds=set(payload.kinds))
# WsListener 将匹配的 eventbus 事件转成 runtime.event frame
```

ack frame（必须在任何 `runtime.event` 之前）：

```json
{
  "id": "r1",
  "type": "runtime.events.subscribe.result",
  "payload": {
    "kinds": ["conversation.cost", "conversation.input", "conversation.output", "gateway.dispatch"]
  }
}
```

服务端 push：

```json
{
  "type": "runtime.event",
  "payload": {
    "kind": "conversation.cost",
    "event": {
      "conversation_id": "c1",
      "input_tokens": 100,
      "output_tokens": 20,
      "payg_cost": 0.0001,
      "estimated": true
    }
  }
}
```

终端条件：连接断开。

Runtime event kind catalog（`lifecycle.md` eventbus 出口）：

| kind | event payload 要点 |
| --- | --- |
| `conversation.input` | `conversation_id`, `content`（`list[ContentItem]` builtins） |
| `conversation.stream` | `conversation_id`, `event`（`StreamEvent`） |
| `conversation.output` | `conversation_id`, `reason`（`stream_stop.reason`） |
| `conversation.tool_results` | `conversation_id`, `count` |
| `conversation.cost` | `conversation_id`, token fields, `payg_cost`, `estimated`, `account` |
| `conversation.history.append` | `conversation_id`, `item`（wrapped `HistoryItem`） |
| `gateway.dispatch` | `route`, `actor_id`, `delivered`, `conversation_id` |
| `incoming.message` | `route`, `text`, `source` |
| `task.started` | `task_id`, `owner`, `kind`, `name` |
| `task.finished` | `task_id`, `owner`, `kind`, `status`, `error`, `exit_code` |
| `actor.blocked` | `actor_id`, `conversation_id`, `reason` |

`gateway.dispatch` payload 示例：

```json
{
  "route": "qq.group.id:232411",
  "actor_id": "amy",
  "delivered": true,
  "conversation_id": "route-c1"
}
```

- `route`：来自 `IncomingMessage.route`
- `actor_id`：route 命中时由 `Gateway.routes` 解析；未命中为 `null`
- `delivered`：是否成功投递到 actor mailbox
- `conversation_id`：来自 `IncomingMessage.conversation_id`；可为 `null`

### `task.subscribe`

客户端：

```json
{
  "id": "t1",
  "type": "task.subscribe",
  "payload": {
    "task_id": "actor:amy"
  }
}
```

Facade contract：

```text
ack task.subscribe.result
ws_listener.track_task(task_id=payload.task_id)
# WsListener：task.eventbus 终态事件 + task.stdout.subscribe() -> task.event frame
```

ack frame（必须在任何 `task.event` 之前）：

```json
{
  "id": "t1",
  "type": "task.subscribe.result",
  "payload": {
    "task_id": "actor:amy"
  }
}
```

服务端 push：

```json
{
  "type": "task.event",
  "payload": {
    "task_id": "actor:amy",
    "status": "running",
    "stdout": ""
  }
}
```

`stdout` 非空时表示增量 stdout chunk；终态帧 `stdout` 为空字符串，`status` 为
`done` | `cancelled` | `error`。

终端条件：task 到达终态后发送最后一帧 `task.event`，或连接断开。

### `task.cancel`

Task 控制可以继续走 HTTP，也可以走 WebSocket command：

```json
{
  "id": "t2",
  "type": "task.cancel",
  "payload": {
    "task_id": "actor:amy"
  }
}
```

Facade contract：

```text
runtime.cancel_task(task_id)
ack task.cancel.result with app.task_snapshot(task_id)
```

ack frame：

```json
{
  "id": "t2",
  "type": "task.cancel.result",
  "payload": {
    "task_id": "actor:amy",
    "status": "cancelled",
    "stdout": "",
    "error": null
  }
}
```

task 不存在时返回 `not_found` error frame。

## Admin HTTP

本地管理入口，供 CLI 和本机运维调用。默认只接受 loopback 连接；非 loopback 返回 401。

```http
POST /api/admin/interrupt   # body: { "conversation_id": "..." } 或 { "all": true }
POST /api/admin/shutdown    # body: {} 或无 body
```

`POST /api/admin/interrupt` 调用 `Yuubot.interrupt(conversation_id)` 或
`Yuubot.interrupt_all()`，打断正在运行的 conversation loop，但不删除 durable history。

`POST /api/admin/shutdown` 触发 ASGI server 优雅关闭：停止接受新连接、cancel 运行中 task、
flush 数据库。

CLI 对应命令：

```text
yuubot status CONFIG [--json]     # 查询服务是否运行
yuubot stop CONFIG                # POST /api/admin/shutdown
yuubot interrupt CONFIG --conversation-id ID
yuubot interrupt CONFIG --all     # POST /api/admin/interrupt
```

`status` / `stop` / `interrupt` 通过读取 run state 文件获得 host/port，再调用上述 admin
endpoint。服务未运行时 CLI 返回 exit code 3。

### Facade: Admin interrupt / shutdown

```text
Entrypoint: POST /api/admin/interrupt
           POST /api/admin/shutdown
Input protocol: interrupt JSON = { conversation_id: string } 或 { all: true }；shutdown 空
  object 或无 body。
Context collection: 校验 client 为 loopback（127.0.0.1 / ::1）；runtime ConversationManager。
Core call: interrupt / interrupt_all / on_shutdown callback。
Output protocol: interrupt 200 { conversation_id, interrupted } 或 { interrupted: string[] }；
  shutdown 200 { status: "shutting_down" }。
Error protocol: 400 bad_request（interrupt body 缺 conversation_id 且 all 不为 true）；
  401 unauthorized（非 loopback）；500 internal_error。
Persistence: 不写配置；interrupt 可能将 conversation status 标为 interrupted 并写
  ConversationRecord。
Compatibility: 可扩展 shutdown grace 参数；interrupt 不支持删除 history。
```

## Auth And Secrets

部署双 base、AdminAuth 不变量、`published/` 磁盘布局见
[`deployment/deployment-design.md`](deployment/deployment-design.md)。
Admin 边界、Inbound、Tasks、Share、KV 见
[`services/README.md`](services/README.md) 及
[`02-admin-boundary.md`](services/02-admin-boundary.md)、
[`03-inbound.md`](services/03-inbound.md)、
[`04-tasks.md`](services/04-tasks.md)、
[`05-share.md`](services/05-share.md)、
[`06-kv.md`](services/06-kv.md)。

yuubot 的服务面需要区分三类入口：

- 本地管理入口：前端配置、CLI 管理命令、Task 控制和 shutdown。默认只绑定 loopback；如果服务
  暴露到非本机地址，必须启用管理认证。
- Conversation 入口：用户对话和文件访问。默认同管理入口共享同一个部署边界；多用户权限不是
  当前 core contract。WebSocket `/api/ws` 与 HTTP 共享同一部署边界；v1 不单独定义 WS 认证，
  除非管理面暴露到非 loopback 地址。
- Integration inbound：外部平台回调或 adapter push。每个 Integration 自己声明认证信号，
  例如 bearer token、HMAC signature 或平台原生签名。

### v1 机制

**Loopback gate（admin）**

`POST /api/admin/*` 在 facade 层校验 client IP 为 loopback：`127.0.0.1`、`::1`（实现可接受
`localhost` 解析结果）。非 loopback 返回 `401 unauthorized`。这是 v1 admin 的硬要求；当前
`src/yuubot/web/api.py` 已实现。

**DeploymentConfig.secrets / SecretPolicy**

`DeploymentConfig`（见 `lifecycle.md`）可携带 `secrets?: SecretPolicy`。Facade 与 runtime 通过
`secret_ref` 解析 secret 值：HTTP/CLI 只持久化 ref 名（如 `api_key_ref`、`secret_refs.access_token`），
不在 bootstrap 或 snapshot 响应中返回明文。`SecretPolicy` 负责把 ref 映射到环境变量、文件或
外部 secret store；bootstrap 只暴露 `configured: true/false` 与 `last_error`。

**非 loopback 管理 HTTP 认证（设计决策，v1 要求）**

当 `DeploymentConfig.server.host` 不是 loopback 地址时，所有 management HTTP（config、bootstrap、
conversation query、file、runtime/task snapshot、admin 以外的写操作）必须要求认证。v1 不规定具体
token 格式，但 facade 必须在非 loopback 绑定下拒绝未认证请求（`401 unauthorized`）。loopback
绑定下 management HTTP 可免认证。该要求在 contract 层成立；除 admin loopback gate 外，其余
management 认证尚未全面实现。

**Inbound auth（Integration adapter）**

`POST /api/inbound/{integration_type}` 不在 ASGI 层统一鉴权。Facade 把 `Authorization` header、
query 签名或平台原生 header 交给对应 Integration adapter 校验；adapter 将 `secret_ref` 解析结果
与请求信号比对。校验失败返回 `401 unauthorized`；Integration 未 enable 或 provider 不可用时返回
`503 provider_unavailable`。

Secret 值不通过 bootstrap 返回。HTTP 和 CLI 输入 secret 时保存为 `secret_ref` 或由
`SecretPolicy` 接管；bootstrap 只返回 `configured: true/false`。Facade 拒绝在 durable record 中
保存明文 secret，除非 `SecretPolicy` 明确允许本地明文配置（默认不允许）。

## Integration Surface

Integration 的配置和 enable 状态属于数据库 state；可用 Integration type 属于代码 registry。
Integration 的 realtime 能力属于 ASGI route 或 WebSocket command。

Inbound-only Integration 可以把外部消息转成 `IncomingMessage`：

```http
POST /api/inbound/{integration_type}
```

```json
{
  "route": "qq.group.id:232411",
  "text": "ping",
  "conversation_id": "route-c1",
  "source": {
    "message_id": "..."
  }
}
```

后端执行：

```text
runtime.emit_incoming(IncomingMessage(...))
```

`Gateway` 根据 **Gateway Routes HTTP** 中配置的 route 表找 Actor mailbox（见
`GET/POST/PUT/DELETE /api/routes`）。命中后投递 `ActorMessage`；没有命中就丢弃，并通过
`gateway.dispatch` 事件暴露结果。没有匹配 route 时 `delivered: false`。

成功响应：

```json
{
  "integration_type": "qq",
  "delivered": true
}
```

`delivered` 与 `gateway.dispatch` 事件中的同名字段一致。

### Facade: `POST /api/inbound/{integration_type}`

```text
Entrypoint: POST /api/inbound/{integration_type}
Input protocol: JSON body = IncomingMessage（route, text, conversation_id?, source?）；
  可选 Authorization: Bearer <token> 或其它 Integration 声明的 auth header；校验规则由
  integration_type 对应 adapter 定义（常见模式：Bearer 与 IntegrationRecord.secret_refs 中
  某 ref 解析值比对）。
Context collection: integration_registry.specs()[integration_type]；enabled Integration 实例；
  gateway route 表（已 rebind）；SecretPolicy 解析 inbound secret_ref。
Core call: adapter.validate_inbound(request)（若 adapter 提供）→ runtime.emit_incoming(message)
  → gateway.dispatch。
Output protocol: 200 { integration_type, delivered: bool }。
Error protocol: 400 bad_request（body 非法、route 缺失）；401 unauthorized（auth 信号缺失或
  mismatch）；404 not_found（未知 integration_type）；503 provider_unavailable（Integration 未
  enable 或 adapter 连接不可用）；500 internal_error。
Persistence: 不写配置；成功投递只影响 runtime mailbox 与 eventbus。
Compatibility: IncomingMessage 可追加 source 子字段；auth header 名由 Integration spec 扩展，
  不进入通用 contract。
```

### Implementation notes

当前 `api_inbound` 尚未调用 adapter inbound 校验；contract 要求 facade 在 emit 前完成
per-integration auth。

如果某个 Integration 需要原生 WebSocket，例如平台 adapter 需要保持登录连接，可以注册：

```http
GET /api/integrations/{integration_type}/ws
```

该 route 属于 Integration 自己的协议，不进入通用 conversation stream。通用前端只需要知道
Integration 的配置 schema、configured 状态和 enabled 状态；Integration 专属控制台可以按需
理解该协议。

### Facade: `GET /api/integrations/{integration_type}/ws`

```text
Entrypoint: GET /api/integrations/{integration_type}/ws
Input protocol: Integration-owned WebSocket 握手与 frame 协议；无通用 yuubot WS command catalog。
Context collection: enabled Integration 实例；adapter 持有的连接状态与 SecretPolicy。
Core call: Integration adapter 注册并处理该 route（登录、心跳、平台事件转 IncomingMessage 等）。
Output protocol: 由 Integration 文档定义；不保证与 /api/ws frame shape 兼容。
Error protocol: 由 adapter 定义；连接级失败可返回 HTTP 4xx/5xx 或 WS close code。
Persistence: 由 adapter 决定；yuubot core 不持久化平台 WS session。
Compatibility: 每个 integration_type 独立版本；通用前端不得依赖此 route。
```

## CLI Surface

CLI 是管理员运维入口，不是前端管理 API 的完整镜像。它服务部署、启动、迁移、诊断、停止服务、
中断运行中任务和查看数据库状态。需要配置业务对象时，管理员优先使用 ASGI 管理面或导入工具，
不要求 CLI 覆盖每个 HTTP mutation。

`yuubot chat CONFIG ACTOR MESSAGE [--conversation-id ID]` 是本地调试入口：直接调用
`Yuubot.chat_stream` 并把 `StreamEvent` 打到 stdout。它不是正式前端 contract；正式对话走
`GET /api/ws` + `conversation.send`。

### Implemented

以下命令在 `src/yuubot/cli.py` 中已实现：

```text
yuubot chat CONFIG ACTOR MESSAGE [--conversation-id ID]   # 本地调试，非正式前端 contract
yuubot serve CONFIG [--host HOST] [--port PORT]
yuubot deploy CONFIG [--dry-run]
yuubot check CONFIG [--json]
yuubot migrate CONFIG [--dry-run] [--json]
yuubot status CONFIG [--json]
yuubot stop CONFIG [--json]
yuubot interrupt CONFIG --conversation-id ID [--json]
yuubot interrupt CONFIG --all [--json]
yuubot db info CONFIG [--json]
yuubot version
```

### Planned

以下命令在 contract 中定义，尚未在 `cli.py` 注册；实现时应保持 stdout/stderr 与 exit code
表一致：

```text
yuubot db export CONFIG --out PATH
yuubot db vacuum CONFIG
yuubot upgrade check
yuubot upgrade apply [--version VERSION]
```

CLI stdout 是机器可读或用户请求的结果；stderr 是诊断、进度和错误。`--json` 时 stdout 输出
单个 JSON object。通用 exit code：

| Exit | 含义 |
| --- | --- |
| 0 | 操作成功。 |
| 1 | 操作失败。 |
| 2 | CLI 参数错误。 |
| 3 | 目标服务未运行。 |
| 4 | 配置或 schema 校验失败。 |
| 5 | 数据库被运行中的服务占用。 |

`serve` 是唯一常驻命令。`deploy` 准备本机运行所需目录、schema 和服务元数据；具体是否写
systemd、container 或其他平台文件属于部署适配器。`check` 验证 `DeploymentConfig`、数据库
schema、registry 与必要目录；默认不访问外部 provider。`migrate` 只处理 durable schema。
`status`、`stop` 和 `interrupt` 面向正在运行的服务；实现可以通过本地管理 channel 或
loopback admin endpoint 完成。`db` 子命令是离线维护入口，必须检测运行中服务锁，避免和
daemon 同时写数据库。`upgrade` 管理 yuubot runtime/package 版本；当前安装方式不支持自更新时
返回 `unsupported`，不尝试猜测包管理器。

所有运维命令的具体效果需要记录到开发文档。

## 示例1. 前端启动

1. 前端请求 `GET /api/bootstrap`。
2. 渲染 LLM provider、Integration schema、Actor 列表、最近 Conversation。
3. 用户保存配置时调用对应 `PUT /api/*`；启用或禁用 Integration 时调用对应 `POST`。
4. 后端写数据库并返回新的 bootstrap snapshot。
5. 前端用返回快照刷新页面状态。

## 示例2. 用户对话

1. 前端打开 `GET /api/ws`。
2. 用户选择 Actor，输入消息，发送 `conversation.send`。
3. 后端用 `ConversationManager.get_or_create(actor, conversation_id)` 找到会话。
4. `Conversation.run_loop` 追加用户输入，调用 LLM，执行 Tool，持续产出 `StreamEvent`。
5. WebSocket 原样转发 `StreamEvent`；前端 append-only 渲染。
6. `stream_stop` 后，本轮结束。History 已持久化。

## 示例3. 用户打断

1. 用户点击停止，前端发送 `conversation.interrupt`。
2. 后端调用 `Yuubot.interrupt(conversation_id)`。
3. Conversation 设置 `stop_event`。
4. LLM adapter 或 Harness 观察到打断后收尾。
5. 前端收到 `conversation.interrupt.result`，随后收到 `reason = interrupted` 的
   `stream_stop`。

## 示例4. Integration 入站消息

1. 管理员通过 **Gateway Routes HTTP** 配置 route，例如 `POST /api/routes` 写入
   `{ "integration_id": "qq", "pattern": "qq.group.id:232411", "actor_id": "amy", "enabled": true }`；
   gateway 立即 rebind route 表。
2. Integration adapter 收到外部平台消息。
3. Adapter 调用 `POST /api/inbound/{integration_type}`（body 含 `route` / `text`），或在自己的
   ASGI route 中直接构造 `IncomingMessage` 并调用 `runtime.emit_incoming`。
4. Runtime emit `incoming.message` event。
5. Gateway 按 route 表匹配 `IncomingMessage.route`，命中后投递 Actor mailbox；未命中则
   `delivered: false` 并仍 emit `gateway.dispatch`。
6. Actor 从 mailbox 取出消息，进入 Conversation loop。
7. 前端若已 `conversation.history.subscribe` 或 `runtime.events.subscribe`，task 完成触发的
   continuation 与入站消息一样，经 eventbus → `WsListener` 推送，无需额外 WS 命令。

## 示例5. Gateway route 配置

1. 前端或运维调用 `POST /api/routes`，创建 route：
   `qq.group.id:232411` → actor `amy`（`integration_id: "qq"`，`enabled: true`）。
2. Gateway `rebind` 内存索引；`GET /api/routes` 可验证 route 表。
3. 外部平台消息经 adapter 转为 `POST /api/inbound/qq`，body：
   `{ "route": "qq.group.id:232411", "text": "hello from qq" }`。
4. Runtime emit `incoming.message`，随后 `gateway.dispatch`（`delivered: true`，`actor_id: "amy"`）。
5. Actor `amy` 从 mailbox 收到 `ActorMessage`，进入 unattended Conversation loop；订阅方通过
   `runtime.event` 或 history append 观察结果。

## Development Handoff

实现者应仅凭以下文档完成服务面开发，无需额外 instruction artifact：

| 文档 | 职责 |
| --- | --- |
| `design.md` | 核心对象、不变量、`ConversationContext`、Harness、Tool、Integration 行为与数据模型 |
| `lifecycle.md` | `DeploymentConfig`、`ApplicationState`、启停顺序、持久化、eventbus 事件、迁移 |
| `integration-design.md` | Integration registry、DB state 合并、Gateway route 表、inbound 数据流 |
| `services/README.md` | 服务扩展实现顺序：admin 边界、inbound、tasks、share、kv |
| `service-surface.md`（本文） | HTTP / WebSocket / CLI / inbound 的 facade contract、wire shape、错误协议 |

阅读顺序：先 `design.md` 理解 core，再 `lifecycle.md` 理解状态如何进入 runtime，
`integration-design.md` 理解 Integration 与 route 边界，`services/README.md` 按序落地
admin / inbound / tasks / share / kv，最后 `service-surface.md` 把能力暴露给前端与外部
Integration。

命名对照：代码中对话上下文类型为 `RuntimeContext`（`yuubot.models`），设计文档称为
`ConversationContext`；二者同一角色——一次 Conversation 的只读上下文树，由
`build_conversation_context`（或等价 helper）构造，挂接 model、conversation、actor、workspace、
otel、rpc、integrations。实现时以代码类型名为准，设计评审时以 `ConversationContext` 为准。
