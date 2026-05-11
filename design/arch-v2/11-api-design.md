# 11. API 设计与安全

> 状态：草案 v1
> 日期：2026-05-04

## 进程职责划分

```
yuubot-admin (port 8781, 对外暴露)
  - 登录认证
  - Admin UI 静态文件
  - /api/* — Runtime Resources 的完整 CRUD 控制面
  - /ws/chat — Admin Web Chat WebSocket
  - POST /api/daemon/reload — 通知 daemon 刷新

yuubot-daemon (port 8780, 仅 localhost)
  - Gateway（消息路由）
  - Actor Runtime（会话执行）
  - Integration Runtime（外部连接生命周期）
  - /agent-fns/*（agent Python 工具调用）
  - GET /healthz — 健康检查
  - GET /api/status — 运行时状态
  - POST /api/admin/reload — 接受 admin 的刷新通知
```

## 认证模型

### Admin 认证（session-based）

Admin 使用登录表单 + session cookie，不依赖每请求手动传 token。

```
POST /api/auth/login
  Content-Type: application/json
  Body: {"secret": "<admin.secret>"}
  
  200: Set-Cookie: yuubot_session=<session_token>; HttpOnly; SameSite=Lax; Path=/
       Body: {"authenticated": true}
  401: {"error": "invalid_secret"}

POST /api/auth/logout
  200: Set-Cookie: yuubot_session=; Max-Age=0
       Body: {"authenticated": false}

GET /api/auth/session
  200: {"authenticated": true}
  401: {"authenticated": false}
```

Session 属性：
- `httpOnly=True` — JavaScript 不可访问
- `sameSite=Lax` — 防止 CSRF，允许同站导航携带 cookie
- `secure=True` — HTTPS 部署时强制
- 默认过期时间可配置（建议 24h）
- Session 存储于内存（单进程），暂不需要持久化

### Daemon 认证（shared secret + localhost-only）

Daemon 进程只绑定 `127.0.0.1`，不对外暴露。Admin 与 daemon 通信时携带 shared secret。

```
POST /api/admin/reload
  X-Daemon-Secret: <daemon_secret>   # 从 bootstrap config 读取
  → 200 / 403

GET /healthz                          # 无需认证
GET /api/status                       # X-Daemon-Secret
```

`daemon_secret` 由 bootstrap config 生成或配置，与 `admin.secret` 独立。

### 部署约束

- `admin.secret` 为空时，admin 只允许绑定 `127.0.0.1`，并打印强警告。
- 部署公网时，admin 必须经 HTTPS 反向代理（Caddy/nginx），`admin.secret` 必须非空。
- daemon **永远不对外暴露**，只通过 admin 间接访问。
- 反向代理负责 TLS termination、rate limiting、请求体大小限制。

---

## Admin API（控制面，port 8781）

所有端点除 `/healthz` 和 `/api/auth/*` 外均需要有效 session。

### 通用约定

- 所有 JSON 响应使用 camelCase 命名（前端友好）
- 时间戳格式：ISO 8601
- 列表端点支持 `?enabled=true/false` 过滤
- 创建/更新返回完整资源对象

### LLM Backends

```
GET /api/llm-backends
  响应: [LlmBackendResource, ...]
  注意: api_key_secret_id 只返回引用的 secret name + masked value

POST /api/llm-backends
  请求体: {
    "name": "openai-main",
    "yuuagentsProvider": "openai",
    "providerOptions": {"base_url": "https://api.openai.com/v1"},
    "apiKeySecretId": 1,           // 可选，引用已有 secret
    "defaultModel": "gpt-4o",
    "defaultStreamOptions": {},
    "modelCapabilities": {"chat": true, "tool_calling": true},
    "models": {"names": ["gpt-4o", "gpt-4o-mini"]},
    "pricing": {"entries": [{"model": "gpt-4o", "inputPerMillion": 2.5, "outputPerMillion": 10.0}]},
    "budget": {"dailyUsd": 10.0}
  }
  201: LlmBackendResource

GET /api/llm-backends/{id}
  200: LlmBackendResource

PUT /api/llm-backends/{id}
  更新可修改字段（name、provider_options、default_model、pricing、budget 等）
  200: LlmBackendResource

POST /api/llm-backends/{id}/test
  发起一次 test connection，返回模型列表和连接状态
  200: {"status": "ok", "models": ["gpt-4o", "gpt-4o-mini"]}
  502: {"status": "unreachable", "error": "..."}

POST /api/llm-backends/{id}/disable
  如果被 Actor 引用，返回 409（禁止禁用）
  200: LlmBackendResource (enabled=false)
```

### Integrations

```
GET /api/integrations
  响应: [IntegrationConfig, ...]

POST /api/integrations
  请求体: {
    "name": "qq-main",
    "pluginId": "qq-napcat",
    "config": {"bot_id": "...", "ws_url": "..."}
  }
  201: IntegrationConfig

GET /api/integrations/{id}
  200: IntegrationConfig

PUT /api/integrations/{id}
  更新 config 字段
  200: IntegrationConfig

POST /api/integrations/{id}/enable
  调用 IntegrationCore.enable(id)，触发 factory.create()
  200: {"status": "enabled"}

POST /api/integrations/{id}/disable
  调用 IntegrationCore.disable(id)，触发 instance.close()
  200: {"status": "disabled"}

DELETE /api/integrations/{id}
  如果仍在启用状态，先自动 disable。
  引用该 integration 的 ActorIngressRule 不会被级联删除；admin UI 应提示管理员清理或修改。
  204: No Content
```

### Prompt Templates

```
GET /api/prompt-templates
  响应: [PromptTemplateResource, ...]

POST /api/prompt-templates
  请求体: {
    "name": "shared.persona",
    "description": "...",
    "content": "你是一个..."
  }
  201: PromptTemplateResource

GET /api/prompt-templates/{id}
  200: PromptTemplateResource

PUT /api/prompt-templates/{id}
  更新 content / description 等字段。bump version。
  200: PromptTemplateResource
```

Prompt Template 只服务 Admin UI 的复制/插入体验，不被 Character 运行时引用。

### Characters

```
GET /api/characters
  响应: [CharacterResource, ...]

POST /api/characters
  请求体: {
    "name": "my-character",
    "description": "...",
    "systemPrompt": "完整 system prompt 文本...",
    "facadeModule": "yuubot.runtime.facade",
    "defaultPromptProviders": [],
    "defaultHints": {"language": "zh-CN", "tone": "friendly"}
  }
  201: CharacterResource

GET /api/characters/{id}
  200: CharacterResource

PUT /api/characters/{id}
  更新 systemPrompt 等字段。bump version。
  200: CharacterResource

POST /api/characters/{id}/clone
  201: CharacterResource (clonedFrom=原character.name)

POST /api/characters/{id}/reset
  仅 builtin character 可用。恢复到内置版本。
  200: CharacterResource
```

### Actors

```
GET /api/actors
  响应: [ActorResource, ...]

POST /api/actors
  请求体: {
    "name": "shiori-web",
    "characterId": 1,
    "llmBackendId": 1,
    "model": "gpt-4o",
    "llmOptions": {"maxTokens": 4096},
    "budget": {"maxUsd": 0.5},
    "agentCapabilities": [
      {"providerKey": "ipykernel", "config": {"sandbox": "restricted"}}
    ],
    "agentPromptProviders": [],
    "allowedCapabilityIds": ["search.query"],
    "runtimePolicy": {"memoryEnabled": true, "rolloverEnabled": true},
    "resourcePolicy": {"concurrencyLimit": 1},
    "defaultPrivate": true,
    "defaultGroup": false
  }
  201: ActorResource

GET /api/actors/{id}
  200: ActorResource

PUT /api/actors/{id}
  更新 policy、model 绑定、permissions。
  200: ActorResource

POST /api/actors/{id}/disable
  200: ActorResource (enabled=false)
```

### Actor Ingress Rules

`ActorIngressRule` 是 v2 唯一的路由配置入口。每条 rule 用 fnmatch glob 匹配 `MessageSource(id, path)` + `kind`，命中则把消息投到目标 actor。

```
GET /api/ingress-rules
  查询参数: ?actorId={id} 按 actor 过滤
  响应: [ActorIngressRuleResource, ...]

POST /api/ingress-rules
  请求体: {
    "actorId": "shiori-web",
    "sourceIdPattern": "web-admin",
    "sourcePathPattern": "dialog:*",
    "kindPatterns": ["*"]
  }
  201: ActorIngressRuleResource

GET /api/ingress-rules/{id}
  200: ActorIngressRuleResource

PUT /api/ingress-rules/{id}
  更新 pattern 或 enabled
  200: ActorIngressRuleResource

DELETE /api/ingress-rules/{id}
  204
```

`ActorIngressRuleResource` 字段：`id`, `actorId`, `sourceIdPattern`, `sourcePathPattern`, `kindPatterns`, `enabled`, `version`。

平台不维护 `channels` 表。"频道"在 UI 上是按 `(integrationId, sourcePath)` 聚合 ingress rule / 历史消息派生的视图，不是独立资源。

每个 enabled actor 会自动获得一条隐式 `system:<actor_id>` rule（用于 actor 间消息和定时触发），该 rule 不出现在 `GET /api/ingress-rules` 列表中，也不可通过 API 修改。

### Secrets

```
GET /api/secrets
  响应列表，每条: {"id": 1, "name": "openai-key", "kind": "api_key", "value": "set:32"}
  绝不返回 ciphertext 或 plaintext

POST /api/secrets
  请求体: {"name": "openai-key", "kind": "api_key", "plaintext": "sk-..."}
  201: {"id": 1, "name": "openai-key", "kind": "api_key", "value": "set:32"}
```

### Bootstrap Config

```
GET /api/bootstrap-config
  只读。返回所有 bootstrap config 值 + 每项的 source / hotReload / restartRequired 标记。
  200: { "admin": {"host": "127.0.0.1", "port": 8781, ...}, ... }
```

### Daemon 通信

```
POST /api/daemon/reload
  Admin 写 DB 后调用此端点通知 daemon 刷新内存中的资源。
  内部实现: HTTP POST → http://127.0.0.1:8780/api/admin/reload
            Header: X-Daemon-Secret
  200: {"status": "reloaded", "ingressRules": 7, "actors": 5}
  503: {"status": "daemon_unreachable"}
```

### Import

```
POST /api/import/legacy
  请求体: {"llmYaml": "...", "dockerConfigYaml": "..."}  (可选)
  一次性导入旧 YAML 配置到 DB Runtime Resources。
  200: {"imported": {"llmBackends": 2, "characters": 3, "actors": 4, "errors": [...]}}
```

---

## Daemon API（运行时，port 8780）

仅绑定 `127.0.0.1`。不对外暴露。

```
GET /healthz
  无需认证
  200: {"status": "ok", "daemon": "127.0.0.1:8780", ...}

GET /api/status
  X-Daemon-Secret: <secret>
  200: {
    "status": "running",
    "actors": {"total": 5, "active": 3},
    "ingressRules": {"total": 7, "enabled": 6},
    "integrations": {"total": 2, "enabled": 2},
    "mailboxes": {"total": 5, "pending": 0}
  }

POST /api/admin/reload
  X-Daemon-Secret: <secret>
  触发: DaemonRefreshDispatcher.refresh(event)
       → RouteBindingService.reload() / ActorManager.reconcile()
       / IntegrationCore.reconcile() 视事件而定
  200: {"status": "reloaded", "ingressRules": 7, "actors": 5}
```

---

## 安全约束

### Secret 处理
- `GET /api/secrets` 只返回 masked value（`set:{length}`），不回显 ciphertext 或 plaintext。
- `POST /api/secrets` 接受 plaintext，服务端立即加密后存入 DB，响应中不回显任何 secret 内容。
- LLM Backend 的 `apiKeySecretId` 只存 secret ID 引用，响应中展开为 `{secretId: 1, secretName: "openai-key"}`。

### 错误脱敏
- 所有 500 错误统一返回 `{"error": "internal_error"}`。
- 详细的异常信息只写入服务端日志，不返回客户端。
- 400/422 校验错误可以返回具体字段级错误信息（如 `{"error": "name is required"}`）。

### Rate Limiting
- 创建类端点（`POST /api/*`、`POST /api/auth/login`）需要 rate limit。
- 登录端点：5 次/分钟/IP。
- 其他写端点：60 次/分钟/用户。
- 读端点不限制。

### CSRF 保护
- Session cookie 使用 `sameSite=Lax`。
- 如果后续引入非 cookie 认证方式，需要 CSRF token。

### 请求体大小限制
- 默认限制 1MB。Prompt Template content 或 Character systemPrompt 可能较大时可适当调高。

### 变更追踪
- 第一阶段不新增 yuubot audit log 层。
- 写操作以 DB transaction 成功为事实来源；需要排查时优先使用 DB/ORM 已有的 transaction log、trigger、history table、changefeed 或服务日志/trace。
- 只有出现明确的恢复、合规或产品查询需求，且 DB 能力无法满足时，才增加应用层 history 表。
- Secret 创建/更新即使进入 trace/log，也不能记录 plaintext。

---

## 跨进程通知

Admin 写 DB 后需要通知 daemon 刷新内存状态。流程：

```text
Admin API 写 DB
  → ResourceRepository 执行 validate → write in transaction → publish ResourceChanged (同进程)
  → HTTP POST http://127.0.0.1:8780/api/admin/refresh
    → daemon 收到通知
      → DaemonRefreshDispatcher.refresh(event):
          actors / actor_ingress_rules → RouteBindingService.reload() + ActorManager.reconcile()
          characters / llm_backends    → ActorManager.forward_resource_change()
          integrations                 → IntegrationCore.reconcile(event)
  → 响应 200 给 admin
```

如果 daemon 不可达（503），admin 端记录 warning 但不影响 DB 写入成功——daemon 重启后会自动从 DB 水化最新状态。

## Web Chat 可靠性与队列表

Admin Web Chat 使用 DB 队列表保证消息可靠性：

```text
Admin UI → WS /ws/chat/{dialog_id}
  → admin 后端收到消息 → 写入 inbound_queue 表 → commit 成功 → ack 前端
  → daemon 异步消费 queue → ingress.emit() → Gateway.ingest() → 路由 → Actor

daemon 离线不影响"消息已收到"的判断。
daemon 重启后重新领取 pending 消息。
```
