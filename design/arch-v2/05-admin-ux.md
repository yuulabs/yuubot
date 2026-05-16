# 05. Admin 用户流程

v2 的 Admin UI 应围绕 Runtime Resources，而不是围绕 YAML 配置项。

## 登录

Admin 使用 session-based 登录。首次访问时展示登录表单，输入 `admin.secret`：

```text
登录页：
  - 输入框（password 类型，不回显）
  - "登录" 按钮
  - 失败提示："密钥不匹配"

POST /api/auth/login  → 200 + Set-Cookie: yuubot_session=...
                       → 401 密钥错误

GET  /api/auth/session → 200 {"authenticated": true} | 401
POST /api/auth/logout  → 清 session
```

Cookie 属性：`httpOnly=True`、`sameSite=Lax`、HTTPS 部署时 `secure=True`。所有 `/api/*` 端点（除 `/healthz` 和 `/api/auth/*`）需要有效 session。

## 首次启动

系统不自动创建 Runtime Resources。空库首次进入 Admin UI 时展示 setup flow，由用户显式创建或导入资源。

```text
LLM Provider templates
  OpenAI template
  OpenAI-compatible template
  Anthropic template
  Gemini template
  DeepSeek template
  Ollama template

Builtin Characters
  shiori
  yuu
  general
  mem_curator

Builtin Actors
  shiori-web
  yuu-group

Builtin Channels
  web-admin-chat enabled
```

如果没有任何可用 LLM provider 或 Channel：

```text
UI status = setup_required
UI 引导用户创建 provider、character、actor、web channel
```

Web Channel 首屏不应只提供一个固定聊天框。它至少支持创建多个 Web dialog：

```text
Dialog: shiori-private-test
  context.key = web/dialog:shiori-private-test
  kind = private
  metadata = {"purpose": "route-test", "target": "shiori"}

Dialog: yuu-group-test
  context.key = web/dialog:yuu-group-test
  kind = group
  metadata = {"purpose": "route-test", "target": "yuu"}
```

这样在只有 Web Channel 的 v2 core 中，也可以通过不同 dialog 验证 Route rules、default actor 和 context pin/reassign 是否按预期工作。

## 用户路径

### 1. 创建 LLM Provider

入口：`Providers -> LLM Providers -> New`

用户可以：

- 选择内置 LLM 模板并填 key。
- 创建 OpenAI-compatible provider，填写 base_url、api_key、models。
- 点击 `Test Connection`。

表单由 LLM provider spec 生成，典型字段：

```text
name
base_url
api_key / oauth
models
default_model
model_capabilities
pricing / budget
```

### 2. 创建 Integration Provider

入口：`Providers -> Integrations -> New`

用户可以：

- 选择 search、GitHub、Linear、W&B、SwanLab 或自定义 integration。
- 按 factory 自己声明的 config schema 填表或走 OAuth。UI 在进入此页面时通过 `GET /api/integration-kinds` 拉取所有 kind 的 JSON Schema（字段 title/description 由 `msgspec.Meta` 携带），据此渲染表单；不再维护第二份前端字段定义。
- 查看 provider 暴露的 capability manifest。
- 点击 `Test Connection`。

Core 只展示 schema、保存 config/secret，并记录验证结果；具体服务含义由 integration 自己实现。

### 3. 创建 Character

入口：`Characters -> New`

用户可以：

- 选择内置 Character 克隆。
- 从空白创建。
- 从 Prompt Template 插入文本，或直接编辑完整 system prompt。
- 选择 facade / tool surface。

Character 不绑定模型。模型在 Actor 中选择。

### 4. 创建 Actor

入口：`Actors -> New`

Actor 是“分配资源后的可运行 Agent”。

表单：

```text
name
character
primary llm provider + model
fallback llm provider + model
bot kind: private / group / both
default private: yes/no
default group: yes/no
runtime policy:
  memory
  memory curator
  rollover
  summarization interval
  max turns
  capability permissions
resource policy:
  budget
  concurrency
  bridge node access
  workspace access
```

创建完成后，Actor 可以被 Gateway 的 Channel / Route 引用。

### 5. 接入 Channel

入口：`Channels -> Connect`

v2 core 只内置 Web Channel。下面的 Discord 流程是 adapter contract 的示例，不代表 core 必须随主仓维护所有平台。

```text
channel_type = discord
auth = OAuth / bot token
guild allowlist
private default actor = shiori-discord
group default actor = yuu-discord
route policy = system default / custom
```

完成后：

```text
Discord message
  -> Discord ChannelAdapter
  -> Gateway
  -> Context
  -> Route
  -> Actor
```

用户即可在 Discord 里与 bot 对话。

## 页面建议

```text
Dashboard
  daemon/admin/channel status
  provider health
  recent conversations
  warnings

Providers
  LLM backends
  integration providers
  connection test
  capability manifest preview

Characters
  builtin/custom list
  section editor
  clone/reset

Actors
  model binding
  runtime policy
  resource allocation
  default actor flags

Channels
  connect flow
  auth status
  route defaults

Routes
  system defaults
  channel overrides
  advanced rules

Contexts
  recent contexts
  web dialog create/edit
  pinned actor
  reassign actor
  archive

Monitor
  iframe/proxy to trace UI

Bootstrap Config
  read-only values
  restart-required badges
```

API 端点详细规范见 [11-api-design.md](./11-api-design.md)。每个页面对应一组 REST 端点。安全约束（认证、secret 遮蔽、错误脱敏、rate limit）见 API 设计文档。
