# yuubot 目标设计

本文档是 `design/requirements.md` 的实现目标，也是 review 的标准。

它只做两件事：

1. 说明系统现在应该以什么抽象来理解。
2. 明确哪些过渡胶水应当继续删除，而不是被继续合理化。

不再保留长篇迁移动机、历史对比、或“未来也许会这样”的讨论。文档必须短，必须能直接指导改代码。

## 1. 稳定概念

yuubot 只承认 5 个稳定概念：

1. `Message`
2. `Route`
3. `Conversation`
4. `Capability`
5. `RenderPolicy`

其它概念都只能是实现细节。

如果某个新对象无法自然归入这 5 类，优先怀疑设计过度，而不是继续加层。

## 2. 三种消息形态

同一条消息有 3 种形态，必须分离：

1. transport：OneBot/NapCat 原始事件
2. storage：落库记录
3. render：交给 LLM 的输入

规则：

1. 业务逻辑不直接消费原始 event dict。
2. Conversation 保存的是结构化输入，不是已渲染字符串。
3. render 只在真正发起 agent turn 时发生。
4. `raw_event` 只能作为迁移字段存在，主路径禁止继续从里面取 `group_id`、`sender`、`_extra_events` 这类业务信息。

## 3. 核心模型

### 3.1 Message

`Message` 是业务层看到的来信，不是 OneBot dict。

最小要求：

1. 有稳定的 `ctx_id`
2. 有 typed `sender`
3. 有受控类型的 `segments`
4. 有渲染和回复所需的稳定元数据，例如 `group_id`、`self_id`、`raw_message`
5. 批量续传消息必须以 typed message 列表表达，而不是塞回 `_extra_events`
6. 可以保留 `raw_event` 作为迁移字段，但它不是业务真相

### 3.2 Route

`Route` 只表达“这条消息进入哪条用例”，不负责执行。

系统只允许两类 route：

1. `CommandRoute`
2. `ConversationRoute`

规则：

1. route 判定必须是纯逻辑。
2. route 一旦产出，不应再回退成原始文本重新匹配。
3. `@bot`、auto mode、continue 都只是 route 规则，不是独立架构中心。

命令语法规则也必须固定：

1. 先识别前缀，例如 `y`、`yuu`
2. 再移除前缀
3. 再匹配命令树
4. `yllm` 和 `y llm` 必须进入同一条命令路由

### 3.3 Conversation

`Conversation` 是 yuubot 的统一会话模型。

它吸收这些旧散件：

1. session
2. active flow
3. ping continuation
4. auto mode 当前 agent
5. handoff summary

Conversation 的职责：

1. 持有当前会话状态
2. 持有 pending messages
3. 持有 rollover 后的一次性 `summary_prompt`
4. 管理 `idle -> running -> closed`
5. 对命令层暴露可观察的会话状态，例如 `/yping` 可区分 `无会话 / 运行中 / 已就绪`

规则：

1. running 时收到的新消息进入 `pending_messages`
2. idle 时新消息进入下一轮 turn
3. `summary_prompt` 只消费一次
4. Conversation 保存“收到什么”，不保存“渲染成什么”
5. `/yping` 语义绑定 Conversation 状态：
   无会话时回复 `pong`
   会话 `running` 时回复 `session pong`
   会话存在且 `idle` 时回复 `session ready`

设计目标很明确：

1. 会话规则应当收口在 Conversation 用例层
2. 不应再散落在 dispatcher、executor、runtime 多处拼装

### 3.4 Capability

Capability 是 bot 暴露给 LLM 的内建能力。

统一协议：

1. LLM 通过 `call_cap_cli` 调 capability
2. capability 对外协议是 raw CLI
3. capability 契约来源只能是各自的 `contract.yaml`

每个 action 至少描述：

1. `name`
2. `summary`
3. `usage`
4. `payload_rule`
5. `return_shape`

规则：

1. `README` 讲实现，不讲 LLM 契约
2. prompt 只能从 contract 生成 capability 文档
3. action 可见性必须能按 agent 过滤

### 3.5 RenderPolicy

`RenderPolicy` 决定 LLM 最终看见什么。

它必须集中、显式、可审计。

至少覆盖：

1. 文本如何渲染
2. `@mention` 如何渲染
3. reply 如何渲染
4. 图片如何渲染
5. 用户名优先级
6. 是否附带群名
7. 是否附带记忆提示
8. continuation 如何合并

目标不是“无限配置”，而是“输入稳定、容易 review”。

## 4. Turn 构建链路

yuubot 的一轮 agent 执行必须经过 3 个对象：

1. `TurnContext`
2. `TaskBundle`
3. `RunContext`

含义：

1. `TurnContext`：这一轮有哪些业务输入
2. `TaskBundle`：这些输入最终怎样变成 LLM payload
3. `RunContext`：真正运行 agent 所需的运行时资源

规则：

1. 三者是单向 builder 链
2. 每一步只能增加信息，不能回退成共享全局状态
3. `AgentRunner` 负责驱动这条链，不负责零散字符串手术
4. `AgentRunner` 不应手写超长 `AgentContext(...)`、`AgentConfig(...)` 参数列表；这些构造应当由上下文对象自己收口
5. 运行环境变量如果只是把 bot 上下文传给 agent，就应明确命名为 agent/runtime env，不要伪装成“子进程语义”
6. 临时单步 agent（如 summarizer、vision helper）也应复用统一启动构造，而不是在各处重复 `Session.from_config(...)` 样板

## 5. 模块边界

推荐按 5 层理解代码：

1. ingress：接外部事件，转内部 Message
2. routing：判定 Route
3. conversations：管理会话生命周期
4. rendering：生成 LLM 输入
5. capabilities：执行内建能力

边界要求：

1. ingress 不做 route 和会话逻辑
2. routing 不执行命令和 LLM
3. conversations 不做渲染细节
4. rendering 不直接决定会话状态
5. capabilities 不泄露底层适配器给 LLM

## 6. yuuagents 接口要求

对 `yuubot` 这类宿主来说，`yuuagents` 的主抽象应当是 `Session`，不是 loop 入口函数。

稳定接口应当是：

1. `Session.step(new_input=None)`
2. `Session.fork()`
3. `Session.spawn(handoff=...)`

语义：

1. `step()` 推进到下一个稳定点
2. `fork()` 复制当前语义上下文
3. `spawn()` 创建继承任务语义的新会话，但不继承完整历史

因此：

1. `run/start/continue/resume` 只能是兼容层，不应继续作为宿主主接口
2. yuubot 不应依赖 loop 入口来表达业务语义
3. continuation 合并逻辑应收口到 `Session.step()`
4. rollover 应通过 `Session.spawn()` 表达

### 6.1 usage / cost 可观测性

`yuubot` 依赖 `Session` 暴露稳定的 usage/cost 状态：

1. `last_usage`
2. `total_usage`
3. `last_cost_usd`
4. `total_cost_usd`

其中 rollover / compression 判定看的是“上一轮输入消耗”，不是累计总量。

规则：

1. runtime 判定优先看 `last_usage.input_tokens`
2. 不允许再用累计 `total_tokens` 代替单轮 usage delta
3. `/ycost` 可以继续查 traces.db，但它只服务统计展示，不参与运行时压缩判定

## 7. 当前实现状态

这次重构已经把主骨架搭出来了：

1. `core/types.py` 提供 `InboundMessage` 和 `Route`
2. `daemon/routing.py` 已经独立出纯路由
3. `daemon/conversation.py` 已经是统一会话容器
4. `daemon/render.py` 已经集中管理渲染
5. `daemon/builder.py` 已经引入 `TurnContext -> TaskBundle -> RunContext`
6. `capabilities/` 已经成为唯一内建 capability 层
7. `yuuagents.Session` 已经提供 `step/fork/spawn`

这些是应继续强化的方向，不要回退。

## 8. 仍需继续删除的胶水

当前代码仍可能出现以下过渡残留；看到时应优先删除，而不是继续依赖：

1. `Route` 产出后又回退成命令树重新匹配
2. 会话逻辑散落在 `LLMExecutor`、`Dispatcher`、`AgentRunner` 多处
3. `Conversation` 和运行时对象之间存在双重 source of truth
4. `Session.step()` 已经存在，但外部仍优先调用 `run/start/continue/resume`
5. capability/prompt 层保留旧字段或旧兼容映射
6. 任何自称 `transition shim`、`compatibility shim`、`legacy` 的路径长期存在
7. turn 构建或 render 仍然把 typed message 重新降级成原始 `event` 再加工

判断标准很简单：

1. 如果一层已经有稳定抽象，旧抽象就应当退出主路径
2. 兼容代码可以短期存在，但不应继续污染新接口

## 9. review 标准

评审时，不要只问“能不能跑”，要问下面 4 件事：

1. 这个改动是否让 5 个稳定概念更清晰？
2. 这个改动是否减少了双重表达和兼容胶水？
3. 这个改动是否把边界推向单一事实来源？
4. 这个改动是否让后来人更难写出旧风格代码？

如果答案是否定的，就算功能正确，也不是好重构。
