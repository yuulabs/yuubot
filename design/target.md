# yuubot 目标设计

本文档描述满足 `design/requirements.md` 的目标设计。目标不是“修补现状”，而是用更少的概念重新组织系统，并方便多人分工。

## 1. 总体原则

### 1.1 先减法，再实现

只保留稳定概念：

1. Message
2. Route
3. Conversation
4. Capability
5. RenderPolicy

其它概念都应该是这五个概念的实现细节，或者被它们吸收。

### 1.2 transport、存储、渲染分离

同一条消息会有三种形态：

1. transport 形态：来自 OneBot 的原始事件
2. storage 形态：落盘后的记录
3. render 形态：给 LLM 的输入

这三种形态不能继续混用。

### 1.3 raw CLI 是 capability 的唯一调用协议

yuubot 会给 LLM 提供 tool 和 capability。

这里的约束是：

1. 不是所有 tool 都必须长得像 CLI。
2. 但 capability 这一类内建能力，统一通过 `cap_call_cli` 一类入口，用 raw CLI 调用。

例如：

```text
im send --ctx 12 -- [{"type":"text","text":"你好"}]
mem recall --ctx 12 --query "偏好 猫"
web read https://example.com
```

系统内部可以把它解析成 typed request，但这属于实现细节，不暴露给 LLM。

## 2. 核心模型

### 2.1 Message

Message 是业务层看到的来信，不是 OneBot dict。

建议字段：

1. `message_id`
2. `ctx_id`
3. `chat_type`
4. `sender_id`
5. `segments`
6. `timestamp`
7. `metadata`

这里 `segments` 可以保留结构化片段，但必须是受控类型，而不是任意 dict。

### 2.2 Route

Route 只表示“这条消息接下来走哪条用例”。

建议只有两类：

1. `CommandRoute`
2. `ConversationRoute`

其中：

1. `CommandRoute` 由命令树产出。
2. `ConversationRoute` 表示这条消息要进入会话系统。

额外的 `@bot`、auto mode、continue，都只是 route 判定规则，不再成为独立的大概念。

命令路由还必须遵守一个真实输入约束：

1. 系统支持“区分前缀”，例如 `y`、`yuu`。
2. 常用写法是 `y<command>`，例如 `/yllm`、`/ybot on`、`/yclose`
3. 兼容写法也允许 `y <command>`，例如 `/y llm`、`/y bot on`
4. 路由匹配时应先识别前缀，再移除前缀，对剩余文本做统一命令匹配。

也就是说，“前缀识别”和“命令节点匹配”是两个步骤，但它们必须组合成一个稳定语法，不允许散落在各处用字符串技巧临时处理。

### 2.3 Conversation

Conversation 是统一的会话模型。

它吸收现有这些分散状态：

1. session
2. active flow
3. ping continuation
4. auto mode 当前 agent
5. handoff summary

建议最小字段：

1. `ctx_id`
2. `agent_name`
3. `mode`
4. `state`
5. `history`
6. `pending_messages`
7. `summary_prompt`
8. `started_by`
9. `last_active_at`

建议最小状态：

1. `idle`
2. `running`
3. `closed`

续传规则统一描述为：

1. 如果 conversation 是 `running`，新消息加入 `pending_messages`
2. 当前轮结束后统一合并增量
3. 如果 conversation 是 `idle`，新消息直接进入下一轮

这样就不需要单独的 `ping flow` 概念。

这里还有一个容易做错的约束：

1. `pending_messages` 存的是结构化消息增量，不是已经渲染好的 XML 或字符串。
2. Conversation 层只负责保存“收到了什么”，不负责决定“LLM 最终看见什么”。
3. 渲染只能在真正发起一轮 agent 运行时发生。

rollover 也要纳入同一个模型：

1. 如果某一轮因为 token 过大而 rollover，但这一轮还没有产出最终回复，系统应立即以 `summary_prompt` 作为交接前言继续跑下一轮。
2. 这种自动续跑最多只允许发生一次，避免在异常上下文里无限 rollover。
3. 如果这一轮已经产出了最终回复，系统不应再自动续跑，而是把 `summary_prompt` 挂在新 conversation 上。
4. 下一条用户消息到来时，再把 `summary_prompt` 和新消息一起作为新会话的第一轮输入。
5. `summary_prompt` 只消费一次，消费后立即清空，避免重复注入。

### 2.4 Capability

Capability 是 bot 暴露给 LLM 的内建能力。

一个 capability 由若干 action 组成。

例如：

1. `im send`
2. `im search`
3. `mem save`
4. `mem recall`
5. `web read`

每个 action 至少要有：

1. `name`
2. `summary`
3. `usage`
4. `payload_rule`
5. `return_shape`

注意：

1. 这些字段必须集中存放在各 capability 自己的 `contract.yaml`，例如 `capabilities/mem/contract.yaml`，contract 是 capability 文档和 prompt 的唯一事实来源。
2. `README.md` 可以讲实现、开发和运维，但不再承载给 LLM 的调用契约。
3. 这里的 `usage` 是给 LLM 和人看的契约。
4. 不是代码注释。
5. 不是散落 prose。
6. 它描述的是通过 `cap_call_cli` 调用 capability 时的 CLI 契约。
7. prompt 构建时必须支持按 agent 过滤 action，例如 `main` 只能看到 `mem recall/save/show/config`，看不到 `mem delete`。

### 2.5 RenderPolicy

RenderPolicy 决定 LLM 最终看到什么。

它必须是显式配置，而不是散在代码里。

RenderPolicy 至少覆盖：

1. 文本消息如何渲染
2. `@mention` 如何渲染
3. reply 如何渲染
4. 图片如何渲染
5. 用户名优先级
6. 是否附带群名
7. 是否附带记忆提示
8. continuation 如何合并多条消息
9. 输出格式是什么

RenderPolicy 的目标不是“支持无限可配”，而是“让 LLM 输入一眼可见、可审计、可复现”。

### 2.6 TurnContext / RunContext / TaskBundle

为了把 transport、conversation、render、runtime 分开，agent 运行过程需要 3 个显式对象。

#### TurnContext

TurnContext 表示“这一轮要处理的业务输入”。

建议字段：

1. `message`
2. `agent_name`
3. `ctx_id`
4. `user_id`
5. `user_role`
6. `is_continuation`
7. `pending_messages`

它的职责是：

1. 收集这轮已有的输入事实。
2. 让 `Dispatcher`、`ConversationManager`、`LLMExecutor` 传递同一种上下文。
3. 保证直到渲染前，都不把消息降级成随意拼接的字符串。

#### RunContext

RunContext 表示“真正启动 agent 所需的运行时资源”。

建议字段：

1. `task_id`
2. `runtime_id`
3. `prompt_spec`
4. `tool_names`
5. `docker_binding`
6. `subprocess_env`
7. `addon_context`
8. `flow`

它的职责是：

1. 在一次运行内部逐步构建。
2. 显式传给 agent、delegate、cancel 等流程。
3. 避免依赖 `_agent_subprocess_env` 之类的全局可变表回查状态。

#### TaskBundle

TaskBundle 表示“最终交给 LLM 的输入包”。

建议字段：

1. `task_text`
2. `items`
3. `resume_history`
4. `is_multimodal`

它的职责是：

1. 吸收 handoff note、continuation 增量、图片输入等渲染决策。
2. 让 `AgentRunner` 只负责启动，不再负责零散字符串拼接。

这三个对象之间应当使用 builder 串起来：

1. 先从消息和会话构建 `TurnContext`
2. 再从 `TurnContext` 构建 `TaskBundle`
3. 最后从 agent 配置和 `TaskBundle` 构建 `RunContext`

每一步都只能增加信息，不能回退成全局共享状态。

## 3. 模块边界

新设计建议按下面 5 层组织：

### 3.1 ingress

负责接收外部事件。

包括：

1. NapCat / OneBot 接入
2. recorder relay
3. 原始事件转内部 Message

不负责：

1. route 判定
2. 会话状态
3. LLM 渲染

### 3.2 routing

负责决定消息进入哪条业务路径。

包括：

1. 命令树匹配
2. 响应策略判定
3. 产出 `Route`

命令树匹配的输入规则需要明确固定：

1. 先匹配“区分前缀”，例如 `y`、`yuu`
2. 再移除前缀并 `strip`
3. 再对剩余文本做命令树匹配
4. 因此 `yllm` 和 `y llm` 最终应进入同一条命令路由
5. `#agent` 属于命令后的语法，不属于前缀系统

不负责：

1. 执行命令
2. 执行 LLM
3. 直接发消息

### 3.3 conversations

负责所有会话生命周期。

包括：

1. 创建会话
2. 续传
3. 切换 agent
4. 超时
5. 关闭
6. 摘要续传
7. 运行中消息合流

这里是当前系统最大的重构中心。

### 3.4 rendering

负责把 Message 和 Conversation 上下文变成 LLM 输入。

包括：

1. 根据 RenderPolicy 组装渲染结果
2. 产出稳定、可审计的文本
3. 决定 capability usage 文档如何注入 prompt

不负责：

1. 自己查数据库补全业务数据
2. 自己决定会话逻辑

forward message 也遵守同一原则：

1. transport 层只识别“这里有一个合并转发引用”，记成 `ForwardSegment(id, summary)`
2. storage 层单独保存 `ForwardRecord`
3. render 层不展开内部内容，只渲染成 `<forward_msg id="..." summary="..."/>`

这样 LLM 默认只看到一个稳定占位，不会被长转发污染上下文；需要时再通过 capability 显式读取。

### 3.5 capabilities

负责 capability 的定义、usage 契约、raw CLI 解析和执行。

包括：

1. capability registry
2. action usage 文档
3. raw CLI parser
4. action handler

可分为两层：

1. capability contract
2. capability runtime adapter

这样未来不管 action 后面是进程内执行还是别的适配器，都不会影响 LLM 协议。

## 4. 统一消息处理流程

目标流程如下：

1. ingress 收到外部事件
2. 转成内部 Message
3. routing 产出 Route
4. `CommandRoute` 进入命令用例
5. `ConversationRoute` 进入会话用例
6. conversations 决定本轮会话状态和输入边界
7. rendering 基于 RenderPolicy 生成 LLM 输入
8. agent runtime 执行
9. LLM 通过 `cap_call_cli` 这类入口用 raw CLI 调 capability
10. capability runtime 执行 action
11. 结果写回 conversation，并在需要时发送消息

这条流程里没有单独的 `ping flow`、`llm executor`、`addon fake cli runtime` 这些中心概念。

## 5. capability 契约形式

建议每个 capability 都维护一份短契约文档，最好可机器读取，也可人读。

推荐结构：

```yaml
name: im
actions:
  - name: send
    summary: send message to a context
    usage: im send --ctx <ctx_id> -- <message_json>
    payload_rule: raw json message list after --
    return_shape: text
  - name: search
    summary: search message history
    usage: im search --ctx <ctx_id> --query "<words>"
    payload_rule: none
    return_shape: text
```

要求：

1. LLM 看 usage 就能用。
2. 人看 usage 就能 review。
3. prompt builder 可以直接引用。

## 6. RenderPolicy 契约形式

建议 RenderPolicy 也放成一份集中配置。

它不必一开始就做成复杂 DSL，但至少应该集中列出关键选择。

示意：

```yaml
message_format: xml
replace_command_prefix_with_bot_name: true
strip_bot_at: true
include_group_name: true
include_memory_hints: false
reply_style: quote
image_style: local_file_uri
name_priority:
  - alias
  - display_name
  - nickname
  - qq
continuation:
  merge_pending_messages: true
  max_batch_size: 8
```

这样做的目的只有一个：

1. 看配置就知道 LLM 实际会收到什么。

### 6.1 forward message 渲染约束

合并转发消息需要额外约束，否则 storage 和 render 会重新混在一起。

规则如下：

1. Message segment 增加 `ForwardSegment`
2. `ForwardSegment` 只有两个对 LLM 有意义的字段：`id` 和 `summary`
3. summary 由 recorder 生成，不在 render 时临时回查
4. summary 规则：
   1. 取内部前 3 段消息
   2. 串接后的摘要总长最多 100 个字符
   3. “前 3 段”与“100 chars 截断”同时生效，取更短结果
5. render 输出固定为 `<forward_msg id="..." summary="..."/>`
6. reply / browse / search 渲染到转发消息时也使用同一 XML 形态
7. `im read --forward-msg <id>` 返回 forward 内部节点渲染结果，但不递归展开其中再次出现的 `ForwardSegment`

storage 约束：

1. `ForwardRecord` 独立于 `MessageRecord`
2. 因为 forward 内容可能来自别的群、私聊或历史缓存，不能假设它属于当前 ctx
3. `ForwardRecord` 至少保存：
   1. `forward_id`
   2. `summary`
   3. `raw_nodes`
   4. `source_message_id`
   5. `source_ctx_id`
4. recorder 在接收普通消息时如果发现合并转发引用，立即通过 OneBot `get_forward_msg(id=...)` 拉取并落库
5. recorder logging 会把 forward 内容展开打印，最多递归 3 层，仅用于调试，不改变 LLM 默认渲染

## 7. 推荐分工

为了适合并行推进，建议至少拆成 4 个工作流。

### 工作流 A：路由与命令边界

目标：

1. 保留命令树
2. 把命令树从会话、LLM、reply 逻辑中解耦
3. 产出清晰的 `Route`

交付：

1. route 模型
2. route 判定规则
3. 命令树边界文档

### 工作流 B：Conversation 重构

目标：

1. 合并 session / active flow / continuation 概念
2. 明确 conversation state machine
3. 给出统一续传方案

交付：

1. conversation 模型
2. state transitions
3. timeout / close / handoff 规则

### 工作流 C：Capability 契约

目标：

1. 把 addon 提升为 capability 概念
2. 保留 raw CLI 调用方式
3. 定义统一 usage 文档格式

交付：

1. capability contract format
2. raw CLI parser 约束
3. 现有 capability 清单和迁移策略

### 工作流 D：RenderPolicy

目标：

1. 收敛给 LLM 的输入渲染逻辑
2. 提供集中配置
3. 消除散落在多个模块里的隐式格式化

交付：

1. render policy schema
2. message view model
3. prompt input examples

## 8. 落地顺序

建议按这个顺序推进：

1. 先冻结核心概念和命名。
2. 先定义 capability 契约和 render policy 契约。
3. 再重构 conversation 模型。
4. 最后把现有模块逐步迁移到新边界。

理由：

1. capability 和 render 是 LLM 成本与行为最敏感的部分。
2. conversation 是结构性重构，必须建立在前两者已经稳定的前提上。

## 9. 本文档的边界

本文档只定义目标设计，不规定具体代码文件名，也不绑定当前实现细节。

如果某个现有模块无法自然映射到本文档中的概念，优先重命名或拆分模块，而不是修改本文档去迁就旧实现。

---

## 10. 重构落地记录（2026-03）

以下模块已按本文档的目标设计完成重构：

### 新增模块

| 模块 | 对应目标概念 | 职责 |
|------|-------------|------|
| `core/types.py` | Message, Route | 领域类型定义（InboundMessage, CommandRoute, ConversationRoute） |
| `daemon/routing.py` | Route | 纯函数路由判定，从 dispatcher 中解耦 |
| `daemon/conversation.py` | Conversation | 统一会话模型，替代 session + active flow + ping |
| `daemon/render.py` | RenderPolicy | 集中式消息渲染，显式策略配置 |
| `daemon/llm_factory.py` | — | LLM/compressor 构建逻辑，从 agent_runner 中提取 |
| `daemon/bot_info.py` | — | Bot 元信息查询（名称、群名），从 agent_runner 中提取 |
| `capabilities/` | Capability | 内建 capability 的唯一实现层，包含运行时、共享工具、CLI 胶水与文档 |
| `core/errors.py` | — | 统一错误层级（YuubotError, ConfigurationError, CapabilityError, MessageSendError） |

### 删除模块

| 模块 | 原因 |
|------|------|
| `daemon/session.py` | 被 `daemon/conversation.py` 完全替代 |

### capability 收口

`addons/` 与 `skills/` 已收敛到 `capabilities/`。内建能力不再拆成别名层、工具层、CLI 层三套目录，而是按 capability 子包组织。

### 性能修复

- `dispatcher.py`：GroupSetting N+1 查询改为 TTL 内存缓存（60s 刷新）
