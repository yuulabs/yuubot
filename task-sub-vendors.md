# Design: LiteLLM Gateway、受控搜索与 Subagents

## 目标

把当前分散的 Provider、搜索和任务能力整理成一条可运维、可观测的 Agent 路径：

```text
普通对话
  → LiteLLM virtual model
    → deployment fallback
      → LLM

execute_python
  → yb.fixer.ask_gemini() / ask_grok()
    → loopback daemon API
      → hosted-search virtual model
        → OpenRouter / AIHubMix / 原厂 fallback
          → 带引用的综合答案

主 Agent
  → delegate(subagent, model_tier, message)
    → kind=agent Runtime Task
      → 隔离子会话
        → task.finished
          → developer notice 回投父 conversation
```

本设计分四部分：

1. 用用户管理的 LiteLLM Proxy 取代 yuubot 自有 Provider/Route 控制面。
2. 提供有明确限额、能力校验和引用结果的 `yb.fixer` 搜索问答。
3. 在 Runtime Tasks 上增加异步、单层 fan-out 的预定义 subagents。
4. 增加可选 Global Skills，并明确 workspace 中一次性交付物与长期项目的归属。

## 原则与所有权

| 能力 | 权威所有者 | yuubot 的职责 |
| --- | --- | --- |
| deployment、密钥、重试、fallback、搜索支持、上游费用 | LiteLLM Proxy | 通过 OpenAI-compatible API 调用和探测 |
| Actor persona、workspace、启停状态、virtual model 选择 | yuubot | 持久化和校验选择 |
| 对话、工具、Runtime Task、投递和取消 | yuubot Runtime | 执行、隔离、回投和观测 |
| fixer/search 单轮限额 | yuubot Runtime | worker namespace 快速拒绝，Runtime 做不可绕过的最终计数 |
| Global Skills 候选内容 | yuubot 管理面 | 复制安装到 workspace，不覆盖本地副本 |
| workspace 文件内容 | workspace | Agent 按 `artifacts/`、`projects/` 和 `AGENTS.md` 规则维护 |

关键边界：yuubot 不引入 LiteLLM Python SDK，不保存 deployment 配置或上游密钥，也不实现另一套 fallback、价格或预算策略。LiteLLM Proxy 由用户独立运维；yuubot 只提供状态探测、告警和可选部署脚本。

文档关系：Phase A 已删除旧 Provider/ModelCard 控制面文档，本设计是 Gateway、fixer 和 subagent 的权威说明；Phase C 是对 `design/services/04-tasks.md` 的 `kind="agent"` 扩展，不改变既有 shell task 语义；限额重置沿用 `design/services/08-python-kernel.md` 的 turn 结束 lifecycle。

## 1. LiteLLM Gateway

文档：https://docs.litellm.ai/

.tmp/external-context/litellm

看清三方文档再动手。尽量添加依赖，不要自己造轮子。

### 1.1 目标场景

```text
管理员在 LiteLLM 配置 deployment、fallback 和 virtual key
  → 管理员在 yuubot Gateway 页面保存 URL 与 virtual key
    → yuubot 加密持久化 key 并立即探测 virtual model 目录
      → Admin 显示连接、模型、搜索能力和最近错误
        → 管理员为 Actor 选择一个可用 virtual model
          → Actor 才可启用并开始对话
```

运行时路径：

```text
Conversation 生成 OpenAI-compatible 请求
  → GatewayClient.stream(actor.virtual_model)
    → LiteLLM 选择 deployment / retry / fallback
      → yuubot 映射 stream event、usage 与错误
        → conversation history、cost event 和 trace 可见
```

LiteLLM Proxy 是唯一 LLM 路由控制面。yuubot 的 Gateway client 仍负责把内部消息、图片和 tool specs 编码为 OpenAI-compatible 请求，并把响应转换为 yuubot `StreamEvent`；但它不再了解具体厂商、账号、deployment、定价或 fallback。

### 1.2 配置

Gateway 连接是 Admin 管理的运行期状态，不属于进程启动 YAML：

```text
Admin PUT /api/gateway
  → base_url / timeout 写入 app_gateway_config
    → virtual key 通过 CredentialStore 加密写入 app_credential_secrets
      → 当前进程切换 GatewayClient 并立即 probe
        → 后续进程启动从数据库恢复并 probe
```

约束：

- `base_url` 指向 LiteLLM Proxy 的 OpenAI-compatible base URL。
- key 使用 daemon credential key 加密；Admin API 和日志永不回显其值，只暴露 `has_api_key`。
- yuubot 不接受 Actor、请求或 `delegate` 工具传入任意 endpoint/key。
- Proxy 不可达时 daemon 仍可启动并提供 Admin；依赖 LLM 的 Actor 保持 disabled/blocked。
- LiteLLM 与 yuubot 没有启动顺序要求；保存连接不需要重启 yuubot。

### 1.3 Virtual model 契约

Proxy 必须提供以下稳定别名：

| virtual model | 用途 | 必需能力 |
| --- | --- | --- |
| `fast` | 低成本、范围明确的 subagent | tools 与父任务所需输入类型 |
| `intelligent` | 高难推理、审查和综合 subagent | tools 与父任务所需输入类型 |
| `ask-gemini` | Gemini 风格的 hosted-search fixer。背靠Google, go-to | `supports_web_search=true` |
| `ask-grok` | Grok 风格的 hosted-search fixer。能查Twitter posts，涉及到twitter必用。 | `supports_web_search=true` |

Actor 主模型不是固定别名：管理员可从 Proxy 返回的 virtual model 目录中选择任意模型。Actor 只保存 selector 字符串，不保存 deployment 或能力快照。

`fast`、`intelligent` 缺失时只阻塞对应 delegate tier；`same` 仍可使用父 Actor 模型。`ask-gemini` 或 `ask-grok` 缺失或搜索验证失败时，只禁用对应 facade。Actor 主模型缺失时阻塞该 Actor。

### 1.4 启动探测与状态

启动探测分三层：

1. `GET /models`：确认 Gateway 可认证并读取 virtual model 目录。
2. `GET /model_group/info`：确认必需别名存在，并检查 `ask-*` deployment 声明 `supports_web_search`。
3. 对每个 `ask-*` 发送一次最小真实请求，携带 `web_search_options`，确认结果包含 hosted-search 引用或等价来源字段。

第三步必须真实验证搜索，不能仅相信静态声明。LiteLLM 对统一 `web_search_options`、能力路由和搜索费用统计的支持见 [LiteLLM Web Search](https://docs.litellm.ai/docs/completion/web_search)。

探测结果保存在进程内 Gateway 状态中：

```python
class GatewayModel(msgspec.Struct, frozen=True):
    id: str
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_web_search: bool | None = None

class GatewayStatus(msgspec.Struct, frozen=True):
    connected: bool
    models: list[GatewayModel]
    fixer_gemini_enabled: bool
    fixer_grok_enabled: bool
    fast_delegate_enabled: bool
    intelligent_delegate_enabled: bool
    checked_at: str
    last_error: str | None = None
```

能力未知按“不支持”处理，不静默退化。探测失败时保留最近一次成功目录只供 Admin 诊断，不允许它绕过本次运行的能力阻塞。Admin 可手动触发重新探测。

### 1.5 Admin surface

现有 Provider 页面替换为 Gateway 页面，不复制 LiteLLM deployment 编辑器：

- Gateway URL、virtual key（只写）、timeout 的配置表单；
- 连接状态、`has_api_key` 和最后探测时间；
- virtual models 目录及 tools/vision/web-search 能力；
- `fast`、`intelligent`、`ask-gemini`、`ask-grok` 契约状态；
- 最近错误和“重新探测”操作；
- 跳转到外部 LiteLLM 管理面的可选链接。

建议 API：

```http
GET  /api/gateway
POST /api/gateway/probe
GET  /api/gateway/models
```

删除 Provider protocol、Provider CRUD、balance、catalog refresh 和 ModelCard CRUD API。Actor 编辑页直接读取 `/api/gateway/models`，只提交 `model: str`。

### 1.6 Gateway client 与请求元数据

所有 chat/fixer/delegate 请求走同一个 `GatewayClient`。请求传播：

```json
{
  "model": "virtual-model",
  "metadata": {
    "trace_id": "...",
    "actor_id": "amy",
    "conversation_id": "c-123",
    "purpose": "chat"
  }
}
```

`purpose` 只允许 `chat | fixer | delegate`。delegate 额外携带 `task_id`、`parent_conversation_id` 和 `subagent`；fixer 携带 facade 名称。不得在 metadata 中发送 persona、用户正文、密钥或完整 conversation history。

Gateway 响应中的 token、实际 deployment/model、fallback 信息、搜索请求数、延迟和费用进入当前 chat span、conversation/task 关联的 cost event。上游没有返回的字段保持 unknown，不由 yuubot 本地价格表推算。

错误统一映射为可观测且可操作的类别：

| Gateway 结果 | yuubot 错误 |
| --- | --- |
| 401/403 | `gateway_auth_failed` |
| selector 不存在 | `gateway_model_unavailable` |
| rate limit / capacity | `gateway_temporarily_unavailable` |
| timeout / network | `gateway_unreachable` |
| hosted search 未实际执行 | `hosted_search_unavailable` |
| 其他上游错误 | `gateway_request_failed` |

不得把 LiteLLM 返回的敏感 headers 或原始内部 deployment 配置写入用户可见错误。

### 1.7 数据库迁移

这是破坏性迁移，不保留旧 Provider 兼容层：

1. 为 `ActorRecord` 增加/改用 `model: str | null`，删除 `provider` 和 `ModelCard` snapshot。
2. 对现有 Actor 保留 id、name、persona、workspace、tools 等非 LLM 字段，但设置 `model = null`、`enabled = false`，状态说明为“需要重新选择 Gateway model”。
3. 删除 `model_cards` 和 `llm_providers` 表；不导出、不转换、不记录旧 config 中的 key。
4. 删除 legacy import 中创建 Provider/ModelCard 的路径，以及 `llm`/`provider` 字段兼容解码。
5. 删除 ProviderRegistry、ProviderSpec、本地 preset/pricing/balance 逻辑和前后端旧路由。

迁移必须在同一事务内完成。测试数据库中放置可识别的旧 secret，迁移后检查 schema、actor payload、日志和新表内容均不含该 secret。

### 1.8 运维脚本

仓库提供参考 Compose、LiteLLM config 和：

```text
scripts/deploy_litellm up
scripts/deploy_litellm down
scripts/deploy_litellm status
scripts/deploy_litellm logs
```

脚本职责：

- 检查参考目录中的 `.env`、master key、数据库和模型配置是否齐全；
- 调用 Docker Compose；
- `up` 后等待健康端点并输出 Gateway URL；
- `status` 同时展示容器状态和 HTTP 健康状态；
- `logs` 透传用户指定的常见日志参数，且不打印 `.env` 内容。

脚本是运维快捷方式，不由 daemon 调用。yuubot 启停、Actor lifecycle 或 Gateway probe 都不得自动启动、停止或重启 Proxy。

在相关docs中说明清楚用法。

## 2. `yb.fixer` 与普通搜索限额

yb.fixer: 超出模型自身能力而紧急召唤救火队长的地方。

### 2.1 Facade contract

```python
class Citation(msgspec.Struct, frozen=True):
    url: str
    title: str = ""

class Answer(msgspec.Struct, frozen=True):
    text: str
    citations: list[Citation]

async def ask_gemini(prompt: str) -> Answer: ...
async def ask_grok(prompt: str) -> Answer: ...
```

`Answer` 只包含可供 Agent 使用的答案与规范化引用。token、费用、deployment、fallback、搜索次数和 trace id 属于观测数据，不进入返回值。

### 2.2 调用场景

```text
Agent 在 execute_python 中调用 await yb.fixer.ask_gemini(prompt)
  → worker namespace guard 检查本轮 Gemini 配额
    → loopback daemon API 绑定 actor/conversation/trace
      → GatewayClient 请求 ask-gemini + web_search_options
        → 验证确实执行 hosted search 并规范化引用
          → 成功后消耗 Gemini 本轮名额
            → Answer(text, citations) 返回 notebook
```

facade 的固定 system prompt 要求：

- 一次覆盖 prompt 中的全部子问题；
- 输出不依赖后续追问、可独立使用的综合答案；
- 区分事实、推断和未知；
- 保留支持结论的来源 URL 与标题；
- 不要求模型输出特定厂商私有 JSON；优先读取 API 的结构化 annotation/citation 字段。

prompt 不能为空，并设置合理长度上限。facade 不接受 model、endpoint、key、search 开关或任意 system prompt。

### 2.3 限额语义

每个 user turn：

| facade | 成功次数上限 |
| --- | ---: |
| `ask_gemini` | 1 |
| `ask_grok` | 1 |
| `yext.web.search` | 3 |
| `yext.web.read` / `download` | 不计数 |

guard 安装在 IPython `user_ns` 的保留对象中，使同一 turn 内的多次 `execute_python` 共享计数；用户代码不能通过重新 import facade 绕过。`harness.close()` 调用 `reset_or_recycle()` 后 namespace 清空，下一条 user message 获得新配额。warm worker 进程可复用，但旧计数不可复用。

计数规则：

- 参数校验失败、Gateway 不可达、超时或 hosted-search 能力失败不消耗成功名额；
- 收到有效 `Answer` 后原子标记成功，防止同一 turn 并发调用绕过限制；
- 同一 facade 的并发调用只允许一个进入执行，其余得到明确的 `fixer_limit_reached`；
- `ask_gemini` 与 `ask_grok` 分开计数，可以各成功一次；
- `yext.web.search` 第四次返回 `search_limit_reached`，提示合并问题或改用尚有配额的 fixer。

限额是防止 Agent 无目的反复搜索的体验 guard，不是账单预算或跨用户速率限制。

### 2.4 Loopback API 与安全

建议 daemon endpoint：

```http
POST /api/fixer/gemini   # loopback only
POST /api/fixer/grok     # loopback only
```

body 只含 `prompt`；actor、conversation、turn、trace 和 purpose 由注入到 worker 的受信上下文补齐。daemon 再次校验 actor enabled、facade capability 和当前 conversation 所有权。外部 Admin API 不暴露 fixer 调用入口。

即使 facade guard 被 workspace 代码篡改，daemon 仍应维护 turn-scoped 次级计数，避免直接构造 loopback 请求绕过限制。worker guard 提供快速、清晰错误；daemon guard 是安全边界。两者使用同一个 `(actor_id, conversation_id, turn_id, facade)` key，并在 turn 结束或 TTL 到期后删除。

### 2.5 引用规范化

不同 deployment 的结构化来源统一为 `Citation`：

1. 优先读取响应 annotation、citation 或 search result 元数据；
2. URL 规范化、去除 fragment 后去重，保留首次出现顺序；
3. title 缺失允许为空，不通过抓取页面补齐；
4. `text` 中厂商特有引用标记可转换为稳定编号，但不得伪造 URL；
5. hosted search 请求成功但没有任何可验证引用时返回 `hosted_search_unavailable`，不伪装为普通模型答案。

### 2.6 `yext.web.search` 限额

普通 search 的 guard 使用同一 turn context 和 daemon 兜底计数。第三次成功后不影响 `read()` 或 `download()`；第四次错误示例：

```text
This turn has already used yext.web.search 3 times. Combine the remaining
questions into one request, or use yb.fixer.ask_gemini/ask_grok if available.
```

search 的查询失败不计为成功次数，但并发预留、完成和释放必须原子化，避免同时发出超过三个请求。

## 3. Delegate 与 Subagents

### 3.1 用户体验

```text
主 Agent 判断工作可以并行
  → 在同一个 assistant round 发出多个 delegate tool calls
    → Harness 并行执行 tool calls
      → 每次调用立即注册一个 kind=agent Runtime Task 并返回 task id
        → 子任务在隔离 conversation 中运行
          → task.finished 触发现有 TaskDeliveryListener
            → 结果作为 developer notice 回投父 conversation
              → 父 Agent 在当前或下一次 continuation 综合结果
```

`delegate` 是普通对话工具，不要求主 Agent 通过 `execute_python`。注册成功即返回，不等待子任务完成。

### 3.2 固定 subagent 注册表

首版注册表只读、内置，不允许 Actor 或 tool payload 覆盖 persona：

| id | 角色 | 适合任务 | 默认工具倾向 |
| --- | --- | --- | --- |
| `explore` | 检查代码、文件和当前实现 | 定位实现、收集证据、梳理影响面 | workspace 读写工具中以只读检查为主 |
| `web-scout` | 调查外部资料并整理来源 | 文档、近期事实、竞品和来源核验 | 搜索/read/fixer |
| `reviewer` | 独立审查设计或改动 | 找遗漏、风险、测试缺口和反例 | workspace 只读检查及必要搜索 |

这里的“只读注册表”指 persona 定义不可由请求修改，不代表所有 subagent 都只有只读文件工具。子任务继承父 Actor 已启用的工具集合，再移除 `delegate`；实际文件权限仍由工具和 workspace sandbox 控制。persona 明确要求：除非 message 要求产出修改且工具允许，否则只报告证据，不擅自改动。

### 3.3 Tool contract

```python
class DelegateInput(msgspec.Struct, frozen=True):
    subagent: Literal["explore", "web-scout", "reviewer"]
    model_tier: Literal["same", "fast", "intelligent"]
    message: str
```

模型映射：

| tier | selector | 工具描述中的指引 |
| --- | --- | --- |
| `same` | 父 Actor 当前 virtual model | 需要和主 Agent 相同能力或输入支持 |
| `fast` | `fast` | 范围清楚、证据收集、低成本并行工作 |
| `intelligent` | `intelligent` | 高难推理、综合、审查或歧义较大的工作 |

tool schema 禁止任意 model selector、endpoint、temperature 或 persona。`message` 必须自包含，因为子任务不接收父历史；工具描述应明确要求主 Agent把目标、范围、路径、约束和期望输出写入 message。

`delegate` 的完整描述、三个 subagent 的适用范围、三个 tier 的选择建议、四任务上限和禁止递归必须进入 LLM 可见的 tool spec。不得只把这些约束留在 Admin 文案或 Python docstring 中。

返回值：

```python
class DelegateResult(msgspec.Struct, frozen=True):
    task_id: str
    status: Literal["pending", "running"]
```

### 3.4 Task record 与上下文隔离

`RuntimeTaskRecord.kind` 增加 `agent`，agent metadata 至少包括：

```text
parent_actor_id
parent_conversation_id
parent_turn_id
subagent
model_tier
model_selector
trace_id
parent_span_id
```

子任务上下文：

- 共享父 Actor 的 workspace、integrations 和已启用工具；
- 强制移除 `delegate`，因此不能递归 delegation；
- 使用注册表中的 persona，不使用父 Actor persona；
- 只接收 `message`，不复制父 conversation history、system extras 或未提交的 tool state；
- 使用独立、临时的 conversation id/context；不写入普通 conversation 列表和持久 history；
- 产生自己的 trace child span 和 task-linked cost events；
- 输出只通过 task result/stdout 和终态投递回父 conversation。

workspace 共享意味着子任务文件修改对父 Agent立即可见。多个子任务可能写同一文件时，主 Agent必须在 message 中划分路径；Runtime 不提供文件锁或自动 merge。

### 3.5 并发与 fan-out 上限

每个父 user turn 最多成功创建四个 agent tasks。计数在 Runtime 按 `(actor_id, conversation_id, turn_id)` 原子维护，不能只依赖 Harness 是否并行。第五次调用返回 `delegate_limit_reached`。

四个名额在 task 注册成功时消耗；任务之后失败或取消不返还，避免无限重试 fan-out。多个 tool calls 可由现有 Harness 并行提交，scheduler 仍服从全局 Runtime/LLM 并发限制。

### 3.6 生命周期与投递

| 事件 | agent task 行为 |
| --- | --- |
| 父对话 interrupt | 已注册任务继续运行；只中断父当前调用 |
| 显式 task cancel | 取消子 conversation/run loop，终态 `cancelled` |
| Actor disable | 取消该 Actor 全部 pending/running agent tasks；不投递 |
| Runtime shutdown | scheduler 取消并 await 所有 agent tasks |
| 子任务完成 | `task.finished` → developer notice 至多一次回投父 conversation |
| 子任务失败 | 保存清理后的错误并投递失败 notice |
| 父 conversation busy | 沿用 `TaskDeliveryQueue`，空闲后 drain |
| daemon restart | 与 v1 Runtime Tasks 一致，ephemeral task 丢失 |

完成 notice 应是稳定、可解析但适合模型阅读的文本：

```text
Subagent task t-123 finished.
subagent: reviewer
model_tier: intelligent
result:
<subagent final output>
```

失败或取消 notice 不附带 traceback、key 或 Gateway 原始响应。结果过长时在 notice 中截断并提示主 Agent 通过现有 Task API 查询完整 output。

### 3.7 递归防护

首版只允许一层 delegation，采用两道防护：

1. 构建子任务工具集时无条件移除 `delegate`；
2. Runtime 创建 agent task 时检查调用上下文 `delegation_depth == 0`，否则返回 `recursive_delegation_forbidden`。

不能只靠 persona 提示禁止递归。

## 4. Global Skills 与 Workspace 体验

### 4.1 候选技能

Global Skills 候选库增加两个可选技能：

#### `artifact-web`

- 先判断是一次性交付物还是未来持续维护的项目；
- 简单、真正单文件的页面可使用一个 HTML；非简单页面拆分 HTML/CSS/JS；
- 做基本的版式、色彩、响应式、键盘访问、语义标签和空/错状态整理；
- 交付前检查入口可打开、资源路径正确、无明显控制台错误；
- 避免把样式、脚本、数据和说明无差别堆进 `index.html`。

#### `explain`

- 先识别受众、已有知识和想解决的问题；
- 用触发 → 决策 → 结果的场景解释行为；
- 通过最小充分例子澄清抽象概念；
- 只有关系、层级或时序确实更清楚时才使用图示；
- 区分事实、假设、权衡和建议，避免无目的长篇输出。

技能只是工作流指导，不自动赋予工具、网络或文件权限。

### 4.2 安装模型

```text
用户在 Skills 管理面选择 Global Skill
  → 选择目标 Actor/workspace
    → yuubot 预览目标 .agents/skills/<id>/SKILL.md
      → 文件不存在则复制候选内容
        → workspace 获得可本地修改的独立副本
```

规则：

- 安装目标固定为 `.agents/skills/<id>/SKILL.md`，skill id 通过现有安全路径校验；
- 复制时保留标准 frontmatter、正文和候选版本信息；
- workspace 副本创建后由 workspace 所有，不与全局记录保持引用关系；
- Global Skill 更新不自动覆盖已安装副本；
- 重新安装发现目标存在时，返回 conflict 并要求用户确认 overwrite；
- 确认页显示本地与候选版本差异；v1 不做自动三方合并；
- 删除 Global Skill 不删除任何 workspace 副本。

建议 API：

```http
GET  /api/skills/{skill_id}/install-preview?actor_id=amy
POST /api/skills/{skill_id}/install
```

install body 包含 `actor_id` 与显式 `overwrite: bool`。默认 `false`。

### 4.3 Workspace 基础 prompt

新 workspace 的基础 prompt 明确以下长期规则：

```text
- 一次性报告、网页、图表和导出内容放入 artifacts/<slug>/。
- 未来会继续开发或维护的代码与文档放入内聚的 projects/<slug>/。
- 不在 workspace 根目录散落实现文件；根目录只保留入口和约定目录。
- AGENTS.md 是 workspace 地图和长期约束入口。保持简短，把项目细节、运行说明和设计内容下沉到对应目录。
```

判断场景：

```text
用户要一份本周数据可视化
  → 结果是一次性交付
    → artifacts/weekly-report/

用户要建立以后持续更新的站点
  → 结果有生命周期和源码
    → projects/site/
      → 项目自己的 README / docs 保存细节
        → workspace AGENTS.md 只记录项目位置和长期约束
```

旧 workspace 不强制移动已有文件；新 prompt 从之后的工作开始生效。若 Agent 主动整理旧文件，应保持引用有效并向用户说明移动结果。

## 5. 分阶段实施

各阶段都应保持主干可运行；阶段内删除旧路径，不建立长期双写或 compatibility layer。

### Phase A：Gateway 基础与破坏性迁移

- 实现 `GatewayClient` 的流式文本、tool call、usage 和错误映射；
- 增加环境配置、startup probe、Gateway 状态 API；
- Actor 改为选择 virtual model string；
- 事务迁移并删除 ProviderRegistry、Provider/ModelCard API 与前端页面；
- 将 Provider 页面替换为 Gateway 状态与模型目录页。

完成标准：普通 Actor 对话仅通过 LiteLLM Proxy；代码和数据库中不再存在可用的旧 Provider 控制面或 secret。

### Phase B：Hosted-search fixer 与限额

- 实现 `yb.fixer` 类型、facade、loopback handlers 和引用规范化；
- 实现 worker/daemon 双层 turn guard；
- 给 `yext.web.search` 增加三次上限；
- 将 search/fixer usage、deployment、fallback、latency 和 cost 接入 tracing。

完成标准：两个 fixer 能分别成功调用一次并返回引用；能力缺失时明确失败；下一 user turn 配额恢复。

### Phase C：Agent Runtime Tasks

- 增加固定 subagent registry 和 `delegate` 工具；
- Agent在Prompt中可以看到所有Subagent简介，知道如何调用。
- 扩展 Task record、scheduler runner、查询快照与 delivery formatter；
- 实现独立临时 conversation、工具继承、递归移除和四并发delegate任务上限；
- 接入 trace linkage、成本事件、取消和 shutdown。

完成标准：主 Agent 可一次 fan out 四个delegate，继续响应其他事件，并在任务结束后收到隔离的结果 notice。

### Phase D：Skills 与 workspace 引导

- 增加 `artifact-web`、`explain` 候选内容；
- 实现 workspace install preview/copy/conflict/overwrite 流程；
- 更新新 workspace 基础 prompt 和相关管理面说明。

完成标准：本地修改不会被全局更新覆盖；Agent 能稳定把一次性交付与长期项目放入正确目录。

### Phase E：运维与收尾

- 增加参考 LiteLLM Compose/config、`.env.example` 和部署脚本；
- 删除过期文档、Provider onboarding 文案和 legacy import 路径；
- 完成端到端、迁移和故障注入测试。

## 6. 测试与验收

### Gateway

- 模拟 Proxy 验证流式 text/reasoning、tool call 参数拼接、usage 和终止原因；
- 验证 401、429、timeout、断流和未知 model 的错误映射；
- 验证 `/models`、`/model_group/info` 与真实 search probe 的组合状态；
- 缜密验证 `fast`/`intelligent` 缺失只阻塞对应 tier，`ask-*` 缺失只禁用对应 facade；
- Actor selector 不在当前目录时不可启用；
- 迁移后旧 key 不存在于数据库、日志、actor payload 或导出内容；
- 所有请求带正确的 trace、actor、conversation 和 purpose metadata。
- Actor 可见 prompt/tool specs 中不再出现旧 Provider/ModelCard 操作说明。

### Fixer 与 search

- Gemini/Grok 在同一 turn 中分别只能成功一次，互不占用；
- 失败不消耗成功名额，并发调用不能突破限额；
- 同一 turn 多次 `execute_python` 保持计数，`reset_or_recycle()` 后的下一 turn 重置；
- warm worker 复用不会继承上一 turn 的 guard；
- annotation/citation 多种形状可规范化、去重并保序；
- 无搜索能力、无可验证引用或 search 未执行时明确失败；
- 普通 search 成功三次后第四次失败，`read()`/`download()` 不计数；
- 直接 loopback 请求也受 daemon guard 限制。
- `yb.fixer`、三次 search 上限和合并问题的建议出现在 execute_python 的 LLM 可见 SDK 说明中。

### Delegate

- 三种 persona 与三档模型映射准确，payload 不能选择任意模型；
- 一个父 turn 可并发注册四个 task，第五个被拒绝；
- 子任务只收到 message，不收到父历史或父 persona；
- workspace 与允许的工具正确继承，`delegate` 始终被移除；
- Runtime depth guard 拒绝伪造的递归调用；
- done/failed/cancelled 都形成正确 Task 快照与至多一次投递；
- 父 interrupt 不取消任务，显式 cancel、Actor disable、shutdown 会取消；
- 父 conversation busy 时排队，空闲后投递；
- trace parent/child、task metadata 和 cost attribution 可从 Admin 核对。
- LLM 收到的 `delegate` tool spec 完整说明三种 persona、三档模型、异步返回、fan-out 上限和单层限制。

### Skills 与 workspace

- 安装创建 `.agents/skills/<id>/SKILL.md`，非法 id/path 被拒绝；
- workspace 本地修改不受 Global Skill 更新影响；
- 已存在副本默认 conflict，只有显式确认才覆盖；
- 删除全局候选不删除本地副本；
- 新 workspace prompt 包含 `artifacts/`、`projects/` 和 `AGENTS.md` 规则；
- 行为测试分别覆盖一次性交付和长期项目场景。

### 运维

- `up/status/logs/down` 映射正确的 Compose 行为；
- 缺少 `.env`、关键变量或 config 时给出可操作错误；
- `up`/`status` 健康检查区分容器运行与 Gateway 可用；
- 日志和命令输出不泄漏 key；
- yuubot lifecycle 不会隐式管理 Proxy。

## 7. 明确不做

- 不在 yuubot 中实现 LiteLLM deployment CRUD、fallback 编辑或供应商密钥管理。
- 不引入 LiteLLM Python SDK；只依赖稳定的 HTTP/OpenAI-compatible contract。
- 不保留旧 Provider API、表、ModelCard snapshot 或兼容字段。
- 不为 fixer 降级到无 hosted search 的普通 completion。
- 不让调用方传任意 subagent persona、模型 selector 或递归 delegate。
- 不持久化 subagent conversation；Runtime Tasks v1 仍是进程内 ephemeral。
- 不新增 yuubot 自有预算治理；账单策略归 LiteLLM，yuubot 只记录上游返回的 usage/cost。
- 不自动覆盖 workspace skill，也不自动整理已有 workspace 根目录。

## 8. 默认值与决策

- LiteLLM Proxy 由用户运维，yuubot 负责探测、提醒和提供快捷部署脚本。
- `ask_gemini`、`ask_grok` 每个 user turn 各一次；`yext.web.search` 每轮三次。
- subagents 首版只有 `explore`、`web-scout`、`reviewer`。
- 每个父 turn 最多创建四个 agent tasks，只允许一层 delegation。
- `same` 使用父 Actor 当前 virtual model；`fast` 和 `intelligent` 使用同名固定 virtual model。
- 父 conversation interrupt 不取消已注册任务；显式 task cancel、Actor disable 和 Runtime shutdown 会取消。
- Global Skill 安装采用复制所有权；全局更新与 workspace 副本没有自动同步关系。
