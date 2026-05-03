# 05. Admin 用户流程

v2 的 Admin UI 应围绕 Runtime Resources，而不是围绕 YAML 配置项。

## 首次启动

系统 seed 默认资源：

```text
Builtin Providers
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

如果没有任何可用 LLM provider：

```text
web-admin-chat channel exists
shiori-web actor exists
actor status = needs_provider
UI 引导用户创建 provider 或填 key
```

## 用户路径

### 1. 创建 Provider

入口：`Providers / Integrations -> New`

用户可以：

- 选择内置 provider 模板并填 key。
- 创建 OpenAI-compatible provider，填写 base_url、api_key、models。
- 创建额外服务，例如 web search、GitHub、Linear、W&B。
- 点击 `Test Connection`。

表单：

```text
name
provider_type
base_url
api_key
models
default_model
capabilities
pricing / budget
```

### 2. 创建 Character

入口：`Characters -> New`

用户可以：

- 选择内置 Character 克隆。
- 从空白创建。
- 编辑 prompt sections。
- 选择 facade / tool surface。

Character 不绑定模型。模型在 Actor 中选择。

### 3. 创建 Actor

入口：`Actors -> New`

Actor 是“分配资源后的可运行 Agent”。

表单：

```text
name
character
primary provider + model
fallback provider + model
bot kind: private / group / both
default private: yes/no
default group: yes/no
runtime policy:
  memory
  memory curator
  rollover
  summarization interval
  max turns
  tool permissions
resource policy:
  budget
  concurrency
  bridge node access
  workspace access
```

创建完成后，Actor 可以被 Gateway 的 Channel / Route 引用。

### 4. 接入 Channel

入口：`Channels -> Connect`

示例：Discord

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
  LLM providers
  service providers
  connection test

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
  pinned actor
  reassign actor
  archive

Monitor
  iframe/proxy to trace UI

Bootstrap Config
  read-only values
  restart-required badges
```
