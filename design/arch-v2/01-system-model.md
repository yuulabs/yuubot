# 01. 系统分层与术语

## 总体定位

yuubot 是一个以 LLM Agent 为核心的多渠道 bot / personal AI platform。中期目标是：用户通过 Admin UI 配置 Provider、Character、Actor、Channel 和 Route，然后在 Web、Discord、QQ、Telegram 等渠道中与不同 Actor 对话，并允许 Actor 使用外部服务和 Bridge 节点完成任务。

## 分层架构

```text
External Platforms
  Discord / QQ-OneBot / Telegram / Web / Project Management Webhooks
        |
        v
Channel Adapters
  平台认证、协议解析、发送消息
        |
        v
Gateway
  IncomingMessage -> Context -> Route -> Actor
        |
        v
Actor Runtime
  Character + model binding + tools + memory + policy
        |
        +------------------------------+
        |                              |
        v                              v
Runtime Resources DB             Infrastructure
  providers, actors, routes      trace, bridge, filesystem, docker
```

## 核心对象

### Provider

Provider 是外部能力提供者，包括：

- LLM provider：OpenAI、Anthropic、Gemini、DeepSeek、OpenAI-compatible、Ollama 等。
- Search provider：Tavily、Exa、Brave、Bing、自定义搜索服务。
- SaaS provider：GitHub、Linear、Plane、W&B、SwanLab 等。

Provider 存在于 DB，secret 加密存储，支持在线创建、测试、禁用和切换。

### Character

Character 是人格和 prompt 模板定义，包含：

- name / description
- prompt sections
- facade / tool surface declaration
- builtin base version
- default hints

Character 不应该强绑定某个 LLM model。它描述“这个角色是谁”，不描述“这个运行实例用什么资源”。

### Actor

Actor 是可被 Gateway 路由到的消息消费终端。

```text
Actor = Character + Model Binding + Runtime Policy + Resource Allocation
```

示例：

```text
Character: shiori
Actor: shiori-web
  model = openai/gpt-5.2
  memory = enabled
  rollover = enabled
  default_private = true

Character: yuu
Actor: yuu-discord-group
  model = deepseek/deepseek-chat
  memory_curator = enabled
  sandbox = restricted
  default_group = true
```

### Channel

Channel 是外部消息入口，例如：

- web-admin-chat
- discord-main
- qq-onebot-main
- telegram-personal

Channel 负责认证和平台协议，不负责选择具体 Actor。

### Route

Route 将 Context 或消息规则映射到 Actor。

```text
channel + context + metadata + message properties -> actor
```

Route 可以有系统默认值，也可以有 Channel 级覆盖或精确规则。

### Context

Context 是一个稳定会话，由 `(channel, key)` 唯一确定。

示例：

```text
web/session:admin
discord/guild:1/channel:2/thread:3
qq/group:123456
telegram/chat:789
```

Context 可在首次路由后 pin 到 Actor，避免后续默认路由变化导致老会话突然换人格。

## Bootstrap Config vs Runtime Resources

v2 的核心分界：

```text
Bootstrap Config
  文件/env。只负责启动系统。通常重启生效。

Runtime Resources
  DB。由 Admin UI 管理。支持在线变更。
```

这能避免过去“配置太多、改动路径不一致、热更新语义不清”的问题。
