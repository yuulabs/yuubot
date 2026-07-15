# yuubot 系统设计与外部 Facade

> **Source of truth**
>
> 本文描述当前代码已经实现的系统设计、扩展点与外部接口。发生冲突时，事实优先级为：
> 当前代码、修改日期较新的文档、修改日期较旧的文档。`design/archive/` 仅供历史追溯，
> 不得作为实现依据。

## 1. 从一条消息理解核心系统

yuubot 是一个常驻 daemon。它把来自聊天、Webhook、定时任务和后台任务的输入交给
Actor；Actor 选择或创建 Conversation，再由 Harness 组织 LLM、工具和流式输出。持久状态
与短期运行资源刻意分开：SQLite 保存配置与历史，Runtime 持有进程内对象，Actor workspace
保存模型实际操作的文件。

### 1.1 进程、状态与目录

`Yuubot` 是应用 facade，负责从持久记录构造 Actor、Integration 和路由，并把外部请求转交
给 Runtime。`Runtime` 是进程级资源所有者，持有数据库、Gateway、ConversationManager、
Task、Cron、MCP、Skills、Shares、KV、事件总线和各 Actor runtime。

`config.example.yaml` 中的 `data_dir` 是唯一数据根目录，当前布局为：

| 路径 | 所有者与用途 |
| --- | --- |
| `workspace/{actor_id}/` | Actor 的工作目录，也是 `execute_python` 和 workspace 工具的边界 |
| `db/yuubot.db` | SQLite 持久化；配置、Conversation 历史和各类业务记录在这里 |
| `logs/` | daemon 和升级日志；轮转、保留策略由 `resources.logs` 配置 |
| `tmp/` | kernel、工具等临时文件，可按资源策略清理 |
| `kv/` | Actor-scoped JSON 文档，不在 SQLite 中 |
| `published/` | Share 的 copy-on-share 公网快照 |

Conversation 历史是 durable state；活跃 Conversation、kernel worker、Task process、listener
和缓存都是 runtime state，daemon 重启后按持久记录重建或消失。不要把“Conversation 仍在
数据库中”和“Conversation 当前仍有活对象”混为一谈。

### 1.2 入站消息

所有会驱动 Actor 的输入最终都转换为 `ActorMessage`，但入口的认证、路由方式和消息角色
不同。

```text
真实触发源
  -> HTTP / WebSocket / Cron / Task 边界验证输入
    -> Gateway route 或显式 actor_id 选出 Actor
      -> Runtime 投递 ActorMessage 到 actor mailbox
        -> Actor 决定复用指定 Conversation 或创建新 Conversation
          -> Conversation 把 InputMessage 交给 Harness
            -> 流式回复、持久历史、文件或外部副作用变得可观察
```

主要入口如下：

- WebSocket `conversation.send` 带 `actor_id`，可选 `conversation_id`。它是交互式 user
  input；若未指定 Conversation，Runtime 创建一个。
- Admin `POST /api/actors/{actor_id}/inbound` 直接指定 Actor，可选择绑定已有
  Conversation。它供本机自动化和 Admin 页面提交使用。
- Public `POST /webhooks/app/{integration_type}` 先由 Integration adapter 验签、规范化为
  inbound envelope，再通过 route table 选择 Actor。公网请求不能绕过 adapter 直接指定
  任意内部对象。
- Cron `actor_message` 像 webhook 一样发送无 Conversation 绑定的普通 user input；
  `conversation_callback` 必须绑定 owner Conversation，并作为 developer continuation。
- Runtime Task 的 `conversation` delivery 唤醒 owner Conversation；`actor` delivery 投递到
  Actor mailbox；`manual` 不主动唤醒任何对象。

普通外部输入不得伪装成 developer/system 消息。developer continuation 只用于系统已经拥有
上下文的续作，例如 Conversation 创建的 Task 或 callback 完成。

### 1.3 Actor、mailbox 与 Conversation

Actor 是长期身份和执行配置：模型选择、system prompt、工具集合、workspace、Integration
上下文和 kernel pool 都以 Actor 为边界。每个 enabled Actor 有 mailbox loop；来自 webhook、
cron、task delivery 或 inbound API 的消息在这里串行进入对话决策。

Conversation 是一条持久对话线程。数据库保存 history；Conversation runtime 对象负责当前
执行、interrupt、listener 和 busy 状态。处理输入时：

1. 指定 `conversation_id` 时加载已有 Conversation；未指定时由 Actor message loop 按消息
   类型决定创建或复用。
2. 同一 Conversation 已运行时，新的交互请求返回 `conversation_busy`，而不是并发修改同一
   History。
3. Actor 缺失、禁用或构造失败时，入口返回 not-found/unavailable，消息不会静默落到其他
   Actor。
4. interrupt 取消当前 turn，但不删除持久 History。删除 Conversation 是独立管理操作。
5. daemon 重启后，Conversation 可从数据库重新加载；之前的 kernel 内存和 runtime listener
   不会恢复。

### 1.4 Harness：一次 turn 的执行容器

Harness 是一次 Conversation turn 内 LLM 与工具的协调器，不是持久会话。Conversation 创建
Harness 时提供当前 `ConversationContext`、Gateway client、工具实例、History 和输出 stream。

```text
InputMessage
  -> Conversation 追加输入并创建 Harness
    -> Harness 读取 system prompt + 当前 tool specs + History
      -> Gateway 选择 endpoint/model 并开始流式生成
        -> 普通文本写入 TextStream
        -> tool call 交给已构造的 Tool
          -> tool result 追加到 History，再交回 LLM
      -> 最终输出和 usage 持久化
    -> Harness 关闭工具；Conversation 离开 running 状态
```

History 的持久前缀包含 `tool_specs` 和 `system_prompt`，交互历史与前缀分开查询。恢复旧对话
时，当前可用 tool specs 会替换旧前缀，避免模型继续调用已经不存在的工具定义。HTTP history
面向 UI 时不把内部前缀当成普通聊天消息展示。

工具 description 与输入 schema 是模型理解能力的正式入口：registry 根据 `ToolSpec` 生成
OpenAI function-tool schema，随 LLM 请求发送。Python facade 没有 function-tool schema，必须
通过 system prompt 的 Integration SDKs 和 Tool Suggestions 注入准确说明。

Harness 关闭时释放 turn-scoped 工具资源。失败会进入 error stream 并留下可诊断日志；取消
和 cleanup 失败不能把 Conversation 永久留在 running 状态。

### 1.5 Runtime Task 与 Cron

Task 系统把“可能比一个 tool call 更久的工作”从 Conversation stack 中拿出来。当前 Runtime
Task 是进程内任务记录，不是 durable job queue；它保存 owner、状态、PTY/stdout buffer、
退出码、delivery 和过期时间。

| 类型 | 创建方式 | 生命周期 | 完成通知 |
| --- | --- | --- | --- |
| `bash` 自动 detach | `bash` tool 超过 idle/hard ceiling | Runtime 内，输出通常保留至 TTL | 不自动续聊，返回 task id |
| 显式 shell task | `yb.tasks.submit(...)` | Runtime 内，`ttl_s <= 3600` | `manual`、`conversation` 或 `actor` |
| Delegate task | `delegate` tool | Runtime 内异步子 Agent | 父方通过 task 结果消费 |
| Cron job | `yb.tasks.cron.add(...)` | SQLite durable，scheduler 重启恢复 | actor message 或 conversation callback |

Task 状态通过 HTTP、WebSocket 或 `yb.tasks` 查询。PTY task 可接受 stdin，也可取消。stdout 是
有上限、会过期的 offload buffer，不是长期存储；长任务应把 checkpoint 和产物写入 workspace。

Runtime Task 组成显式树。task 创建时记录 `parent_task_id` 和 `root_task_id`；自身逻辑已经返回但
仍有活跃后代时进入 `waiting_children`，只有自身与整棵子树都结束后才进入 terminal。child 结束
前先登记内部 delivery，delivery 唤醒直接父 task 后才释放，因此 child 完成与父恢复之间不会出现
整棵树短暂静默的竞态。child 的失败或取消作为结果交给父逻辑处理；取消父 task 会级联取消全部
后代。只有 root task 向 Conversation 或 Actor 投递最终通知，内部节点只唤醒父 task。

以 conversation delivery 为例：

```text
LLM 调用 yb.tasks.submit(delivery="conversation")
  -> daemon 注册 shell process 和 owner Conversation
    -> tool 立即返回 task id，当前 turn 可以结束
      -> process 退出，Runtime 封装完成状态与输出摘要
        -> TaskDeliveryListener 投递 developer continuation
          -> owner Conversation 再次运行 Harness
            -> 用户看到后续解释或产物链接
```

若 daemon 在 Runtime Task 完成前退出，该 task 不会恢复；需要跨重启调度时使用 Cron，并让实际
工作具备幂等性。

### 1.6 `execute_python` 与它承载的生态

`execute_python` 是模型的通用编排环境，而不是 daemon 本身的 Python REPL。它在 Actor
workspace 中启动 ipykernel worker，使用 `<workspace>/.venv/bin/python`，支持 IPython 原生
top-level `await`。代码可组合普通 Python 包、workspace 文件、`yb` runtime facade 和已启用的
`yext` integration facade。

```text
LLM 发出 execute_python(code)
  -> Tool 准备 workspace 与 .venv
    -> Actor KernelPool 按 conversation_id 租赁 worker
      -> worker 注入 daemon URL、task owner、turn token 和 Integration 环境
        -> Python 代码 import yb / yext 并异步调用
          -> facade 直接访问外部服务，或经本机 daemon HTTP 访问 Runtime
            -> stdout/stderr/异常作为 tool result 回到 Harness
```

关键边界：

- worker 在同一 user turn 内可复用，因而变量和 import 可跨多次 `execute_python` 调用；turn
  结束后 session reset，不能依赖内存作为持久状态。
- `uv add`/`uv remove` 改变 workspace 环境后，调用 `restart_kernel` 清掉已加载模块；它不删除
  workspace 文件。
- daemon-managed facade 通过 `YUUBOT_DAEMON_URL` 回调本机 API，并用 owner/turn token 执行
  授权和每 turn 限制。模型不应自己拼接 admin HTTP 来绕过这些 facade。
- enabled Integration 把需要的环境和 prompt docs 注入 worker/system prompt；未启用能力不会
  假装可用。
- kernel 是 headless。图表和其他用户可见产物必须写入 workspace（通常 `artifacts/`），再由
 回复引用文件。
- Python 调用可以在一次代码块内用 `asyncio.gather` 并发编排；多个 `execute_python` tool call
  本身按 Harness 工具执行规则处理。
- 任意 Python continuation 不支持 auto-detach，因为 namespace、控制流和已发生副作用无法安全迁移。
  `yb.fixer.ask_gemini/ask_grok` 由 Runtime 特化承接：先同步等待 30 秒，未完成则返回
  `PendingAnswer(task_id)`、释放 kernel worker，并在后台完成后通过 conversation delivery 续聊。
- kernel worker 错误不会自动重放整段 Python 代码；是否重试由模型根据副作用风险显式决定。

一个完整的数据源场景如下：

```text
GitHub webhook
  -> Public adapter 验签并生成 inbound envelope
    -> Route table 选择维护者 Actor
      -> Actor 创建 Conversation，Harness 获得当前 Integration SDK 文档
        -> LLM 用 execute_python 调 yext.github.repo(...)
          -> facade 使用 daemon 注入的 GitHub 凭据读取 issue/files
            -> LLM 总结结果，History 与 usage 持久化
              -> 回复通过 Conversation stream 对外可见
```

## 2. 扩展点与内置能力

### 2.1 Tool registry

Tool 由名称、payload `msgspec.Struct`、description、factory 和可选 uninstall hook 构成。
Actor record 选择启用哪些 tool config；构造 Conversation 时，factory 获得
`ConversationContext` 与 Runtime。增加工具必须同时保证：schema 可生成、description 足够让
LLM 正确调用、资源能在 Harness close 时释放、所需上下文由声明式 context 提供。

当前内置工具：

| 工具 | 功能与边界 |
| --- | --- |
| `read` | 读取 workspace 文本或图像；限制行数/字节，禁止逃逸 workspace |
| `write` | 覆盖写入 UTF-8 文件并创建父目录 |
| `edit` | 对唯一精确匹配做替换，避免歧义编辑 |
| `bash` | 在 workspace PTY 中执行 `bash -lc`；可流式、detach、stdin、cancel |
| `execute_python` | 在 turn-scoped IPython worker 中运行可 `await` 的 Python |
| `restart_kernel` | 清理当前 Conversation lease 与 Actor idle workers，下一次冷启动 |
| `delegate` | 创建一层异步子 Agent task；子 Agent 不再拥有 delegate |

### 2.2 `yb`：Runtime Python facade

这些包随 `execute_python` 环境预导入。`yb.fixer`、`yb.tasks`、`yb.tasks.cron`、`yb.mcps`
和 `yb.skills` 的 docstring 会进入 system prompt；调用通常经本机 daemon API，因此凭据和
权威状态仍归 Runtime 所有。`yb.office.pdf` 是直接运行在 worker 内的本地 helper。

| 包 | 公开能力 |
| --- | --- |
| `yb.fixer` | `ask_gemini`、`ask_grok`；使用 Gateway hosted-search alias，每种已启用 facade 每 turn 一次成功答案 |
| `yb.tasks` | `submit`、`find`、`list_tasks`；Task 可查询状态/输出、写 stdin、取消 |
| `yb.tasks.cron` | `list_jobs`、`find`、`add`、`pause`、`resume`、`delete`；Cron record durable |
| `yb.mcps` | `search`、`get_client`；client 可列 capability、读 spec、invoke tool、read resource |
| `yb.skills` | `list_skills`、`read`；读取 daemon 合并后的 global/package skill catalog |
| `yb.office.pdf` | `to_markdown`；把 PDF 转为便于模型处理的 Markdown |

`yb.tasks` 与 `yb.tasks.cron` 不是同一持久性级别；`yb.mcps` 不向 worker 暴露 MCP credential；
`yb.skills.read` 是按需加载完整 instruction，不应把整个 catalog 无条件塞入 prompt。

### 2.3 `yext`：Integration Python facade

`yext` 是可选 Integration SDK 命名空间。只有对应 Integration enabled 时，session context 和
使用文档才会进入 Actor 环境。

| 包 | 公开能力 |
| --- | --- |
| `yext.web` | `search`、`read`、`download`；search 每 turn 最多三次成功调用，read/download 用于后续取材 |
| `yext.github` | `repo` 返回 repo facade；当前提供 issue 列表/详情和文件读取能力 |
| `yext.codex` | 官方 Codex CLI 的 `status`、`run`、`cli`、`help` 薄封装 |
| `yext.opencode` | OpenCode CLI 的 `status`、`run`、`cli`、`help` 薄封装 |

Coding CLI facade 运行在 PTY/子进程边界，认证状态会脱敏；需要人工登录时应提示管理员在
Admin Terminal 执行 Integration 给出的命令，而不是把 secret 回显给模型。

### 2.4 Integration extension

Integration registry 以显式 type 注册 spec。spec 负责 typed config、构造 Integration、可选
inbound adapter 和 Admin schema。enabled Integration 可提供：

- `session_context()`：注入 worker 的非秘密上下文或环境引用；
- `prompt_doc()` 或 package docstring：进入 Integration SDKs；
- inbound adapter：把第三方 HTTP 请求验签并转为内部 envelope；
- 生命周期资源：client、PTY、socket 或缓存，随 enable/disable 创建和关闭。

新增 Integration 时，功能描述必须能被 LLM 看见：function/tool 能力写进 tool spec；Python
能力写进 package docstring 或 `prompt_doc`。只在 Admin UI 出现一个配置表单不算完成。

### 2.5 MCP、Skills 与 Gateway

MCP server record 和认证信息由 daemon 管理。启用或刷新 server 后，McpManager 建立连接并
索引 tools/resources/prompts；模型先用 `yb.mcps.search` 发现能力，再按需读 spec 和调用。
OAuth attempt 通过 callback route 回到 daemon，worker 不接触 refresh token。

Skills 来自 daemon 记录、package skills 和 workspace `.agents/skills/`。system prompt 只列摘要
与 inspect hint，模型需要时再读完整 `SKILL.md`。Skill 内容是行为说明，不是可调用 function。

Gateway 管理 OpenAI-compatible endpoints、模型 catalog 与 aliases。Actor 选择 model/alias；
Gateway 负责模型解析、能力检查、stream 和 usage。`fast`、`intelligent`、hosted search 等 alias
只有在 catalog 声明相应能力时才可供 delegate/fixer 使用。

## 3. 外部接口

### 3.1 部署与认证边界

进程配置可声明三个 listener：

| Listener | 面向对象 | 认证 |
| --- | --- | --- |
| `local_admin_server` | 本机 Admin UI/API | loopback 管理面；部分敏感端点显式要求 loopback |
| `trusted_admin_server` | 反代后的远程 Admin | `builtin` session/CSRF 或可信 proxy auth |
| `public_server` | 公网 Share 与 webhook | 不使用 AdminAuth；每个能力自行鉴权 |

本地开发可以把 admin 与 public app 合并到同一服务，但 route classification 不因此改变。
Admin API 使用统一 JSON error envelope；Public app 是显式白名单，未知路径统一 404。

### 3.2 Admin HTTP 与 UI

`/`、`/assets/*`、`/sw.js` 和非 `/api/*` fallback 承载 React Admin UI；`GET /healthz`
返回存活状态。主要 API 按功能域如下。

| 功能域 | 路径族与功能 |
| --- | --- |
| Auth | `/api/auth/login`、`logout`、`session`；builtin session 与 CSRF |
| Bootstrap/runtime | `/api/bootstrap`、`/api/runtime`、`/api/usage`；UI 初始快照和运行状态 |
| Gateway | `/api/gateway`、`/api/gateway/endpoints/*`、`aliases/*`；配置、刷新模型、删除 |
| Actors/workspace | `/api/actors/{id}` enable/disable/delete；`browse`、`files`、`uploads`、目录/移动/重命名/删除 |
| Actor inbound/KV | `/api/actors/{id}/inbound`；`/kv/{key}` GET/PUT/DELETE，支持 ETag/If-Match |
| Integrations | `/api/integrations`、`/{type}`、`/{type}/config|enable|disable` |
| Routes | `/api/routes` CRUD；把 integration/source pattern 映射到 Actor |
| Conversations | `/api/conversations` create/list/detail/history/usage/delete |
| Tasks | `/api/tasks` list/detail/submit/cancel/stdin；submit 受 loopback/turn authorization 限制 |
| Cron | `/api/cron-jobs` list/detail/create/pause/resume/delete |
| Shares | `/api/shares` create/list/detail/delete；创建结果包含 public URL |
| MCP | `/api/mcp-servers` CRUD、enable/disable/refresh/auth；`/api/mcps/*` search/spec/invoke/read |
| Skills | `/api/skills` refresh/create/update/delete、package install/update、copy preview/copy |
| Credentials/auth attempts | `/api/credentials` list/delete；`/api/auth-attempts` CRUD |
| Notifications | `/api/notifications/vapid-public-key` 和 push subscription create/delete |
| Turn facade bridge | `/api/fixer/{facade}`、`/api/web/search`；供有 turn token 的 worker facade 使用 |
| Admin ops | `/api/admin/interrupt`、`shutdown`、`update/status`、`update/apply` |

Actor workspace 中的 HTML 可通过 Admin file route 打开，并以 same-origin JavaScript 调 KV 与
inbound。Public Share 不暴露这些 Admin API，也不会把动态页权限带到公网。

### 3.3 Public HTTP

Public app 仅提供：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /s/{share_id}`、`GET /s/{share_id}/{path}` | 读取 published snapshot；目录优先返回 index，否则生成目录列表 |
| `POST /webhooks/app/{integration_type}` | 按 client/integration 限流，确认 type 和 enabled 状态，由 adapter 验签并投递 |
| `GET /api/mcp-oauth/{attempt_id}/callback` | 完成 daemon 发起的 MCP OAuth attempt |

独立 `public_server` 会要求已支持 webhook 的 Integration signature；secret 来自环境变量。
Public webhook 默认每个 client IP/integration 60 秒 60 次，可信 proxy 只影响 client IP 解析。
历史 `/api/inbound/{integration_type}` 不是现行 public contract。

### 3.4 WebSocket

`WS /api/ws` 是 Admin 实时协议。客户端命令统一包含 `type`、可选关联 `id` 和 `payload`：

- `conversation.send`、`conversation.open`、`conversation.close`、`conversation.interrupt`；
- `runtime.events.subscribe`；
- `task.subscribe`、`task.stdin`、`task.cancel`。

command 的 accepted/result frame 总在该命令触发的 push frame 之前发送。push 包括 Conversation
history/stream/error、runtime event 和 task event。关联 `id` 用于 command response；持续事件由
payload 中的 conversation/task 标识归属。断线会移除该连接的 listener，不删除 Conversation
或 Task。

`WS /api/terminal/ws` 是独立 Admin PTY，供管理员执行需要交互的命令，包括 Integration 登录。
它属于管理面认证边界，不属于 Actor tool 或 Public API。

### 3.5 CLI

`ybot` 与 `yuubot` 指向同一入口：

| 命令 | 用途 |
| --- | --- |
| `chat` | 向指定 Actor 发送一次消息，可指定 Conversation |
| `serve` | 启动后端服务 |
| `dev` | 同时启动后端与前端开发服务 |
| `deploy` / `uninstall` | 安装或移除 systemd/Caddy 部署；支持 dry-run 或删除数据选项 |
| `check` / `status` | 验证配置与依赖，或读取运行状态 |
| `interrupt` / `stop` | 中断指定/全部 Conversation，或停止 daemon |
| `export` | daemon 停止时将整个 `data_dir` 导出为压缩归档 |
| `migrate` | 运行数据库 migration，并可从 legacy DB/config 导入 |
| `db info` | 显示当前数据库路径、schema version、大小和各表行数 |
| `upgrade check` / `upgrade apply` | 检查或安排升级 |
| `version` | 输出当前版本 |

## 4. 文档维护规则

1. 新增或修改外部入口、工具或 Python facade 时，在同一变更中更新本文。
2. HTTP/WS 事实以 route registration 和 wire type 为准；CLI 以 parser 为准；LLM 可见能力以
   tool specs 和最终 system prompt 为准。
3. 阶段计划、未实现 API 和替代方案进入 `design/archive/`，不得混写成当前 contract。
4. 专题文档可以补充并发、消息循环和 kernel 约束，但必须链接本文，且不能另立冲突的 facade
   定义。
5. `yuubot.__init__` 导出的后端类是项目内部 API，不承诺第三方兼容性。

补充材料：

- [Actor message loop](actor-message-loop.md)
- [Data concurrency principles](concurrency.md)
- [Python execution environment](conventions/python-execution-env.md)
- [历史文档归档](archive/README.md)
