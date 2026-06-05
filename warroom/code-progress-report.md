# Yuubot v2 进度汇报 — 场景驱动

---

## 场景

这是 yuubot 的设计目标使用场景（来自 `design/checklist.md` 末尾）：

> 用户在 IM / Linear / GitHub Comment 等地方 at yuubot 的某个 actor 名字，发出消息，
> actor 进行上下文搜寻、定向、编排，编码等专业任务委托给 OpenCode。
> yuubot 的核心功能是上下文搜寻、定向、编排，利用 Overnight 时间做海量信息过滤。
> 常见使用场景：数据分析和项目管理。

这份汇报以**一条完整请求**的端到端追踪，来说明当前代码能跑到哪一步、每一步缺什么。

---

## Scene: 用户在 Telegram 找 AmyActor 帮忙分析一个 Issue

```
1. 用户在 Telegram group "dev-team" 里发消息:
   "@amy help me look at github issue #421 on our backend repo, it's been
    failing in staging since last deploy"

2. 消息到达 yuubot → Gateway 根据 source glob 路由到 AmyActor 的 mailbox

3. AmyActor 启动 agent loop:
   3a. LLM 看到消息，决定调用 yext.github.get_issue("backend", 421)
   3b. Agent 通过 facade bridge → IntegrationCore → GitHubIntegration 获取 issue 内容
   3c. LLM 分析 issue，判断需要看最近部署的 diff
   3d. LLM 调用 yext.github.list_commits("backend", branch="main", since="2 days ago")
   3e. 拿到 commit 列表，LLM 分析可能的问题代码
   3f. LLM 生成诊断结论
   3g. 通过 yb.im.respond() 回复到 Telegram group

4. Telegram 用户看到回复:
   "@amy looked at issue #421: the problem is likely in the DB migration
    PR #1892 which changes the user schema. The staging DB still has old
    schema. Solution: run `rails db:migrate` on staging."

5. 用户可以在线程中继续追问，Actor 持续跟进（利用 prompt caching 的 rollover 机制）
```

---

## Walk: 每一步的代码状态

### 步骤 1: 消息到达

| 系统边界 | 现状 |
|----------|------|
| **Telegram Integration** | ❌ **不存在**。没有 Telegram integration 插件（内置或外部都没有）。 |
| **Gateway 路由** | ✅ 完整。`RouteBindings` + `Gateway.forward()` 按 `fnmatch` 匹配 `source_path_pattern`，投递到 Actor mailbox。测试 `test_route_bindings.py` 充分覆盖。 |

**结论**: 消息连 yuubot 的门都进不了，因为没有 Telegram 集成来接收消息。

---

### 步骤 2: Actor 接收消息并进入 agent loop

| 系统边界 | 现状 |
|----------|------|
| **Actor 生命周期** | ✅ `ActorManager` 管理 start/stop/reconcile，`SimpleLoopActor` 包装 yuuagents 运行时。 |
| **mailbox drain + message loop** | ✅ `SimpleLoopActor._message_loop()` 在每轮 tool execution 之间 drain mailbox，支持 rollover 自动摘要。 |
| **LLM 调用** | ✅ 通过 `assembly.py` 组装 `Stage` + `AgentDefinition`，支持 OpenAI/Anthropic/OpenRouter/DeepSeek/Groq/Google/xAI，含定价和预算。 |
| **PricingAwareLlmClient** | ✅ 记录 token 用量和成本，走 yuutrace 追踪。 |

**结论**: Actor 能启动、能收到消息、能跑 LLM。但因为没有 integration 来投递消息，这一层没有真实数据可以验证。

---

### 步骤 3a-3e: Agent 调用 Integration capability

这是 yuubot 区别于普通 chatbot 的核心能力——agent 可以调用外部工具（GitHub、Linear、Tavily 等）获取上下文。

| 系统边界 | 现状 |
|----------|------|
| **GitHub Integration** | ❌ **不存在**。计划中的内置集成之一，完全未实现。 |
| **Facade bridge (facade → daemon 的 TCP RPC)** | ✅ 架构完整。`IntegrationInvokeBridge` 在 `facade/bridge.py` 中监听 localhost TCP，接收 agent Python runtime 发来的 JSON RPC 请求，转发到 `IntegrationCore.invoke()`。 |
| **Facade codegen (capability schema → `yext.*` Python 代码)** | ✅ `facade/codegen.py` 从 `CapabilitySpec` 生成 Python async 函数。 |
| **IntegrationCore invoke + auth** | ✅ `integrations/core.py` 包含 capability 索引、per-actor 权限缓存、调用时的授权检查。 |
| **System capabilities (yb.*)** | ✅ `yb.im`(IM 回复), `yb.tasks`(后台任务), `yb.delegate`(委派), `yb.schedule`(定时), `yb.actor`(自身状态), `yb.admin`(管理) — 这些 facade 模块已定义在 `assembly.py` 的 `FACADE_IMPORTS` 中。 |
| **唯一可测的 Integration** | ✅ `EchoIntegration` — 提供 `echo.echo` 和 `echo.reply` 两个 capability，用于测试 facade ↔ integration 的完整路径。`test_echo_http_e2e.py` 验证了 HTTP 入口 → Gateway → Actor → LLM → execute_python → yext.echo.echo → bridge → EchoIntegration → 返回结果的完整链路。 |

**结论**: facade ↔ integration 的架构管道是通的（有 `echo` 测试验证）。但**所有真正有用的集成（GitHub、Linear、Tavily、Telegram、Discord）都不存在**。Agent 能调用的只有 echo。

---

### 步骤 3f-3g: Actor 生成回复，通过 IM 渠道发送

| 系统边界 | 现状 |
|----------|------|
| **yb.im.respond()** | ✅ **完整路径已确认**：Actor agent 代码调用 `await yb.im.respond("...")` → facade bridge (`facade/bridge.py:213`) 收到 RPC 后创建 `FacadeImResponse` → 投递到 mailbox → `SimpleLoopActor._send_im_response()` (line 245) 查找消息来源对应的 integration instance → 调用 `instance.response(target_msg_id, msg, react)`。链路完全贯通。 |
| **yb.im.react()** | ✅ 同上，以 `react` 参数发送快速确认（如"working"表情），支持 `ReactionKind` 枚举。 |
| **实际 IM 发送** | ❌ 链路代码存在但无法验证——`instance.response()` 最终调用具体 integration 的 `response()` 方法。当前唯一存在的 integration 是 `EchoIntegration`，它不是 IM 平台。需要 Telegram/Discord integration 实现后才能真正投递。 |
| **Web Chat (对话式)** | ✅ 完整的 Web Chat 通道：`ConversationManager` + SSE 流式推送 + `ChatStore` 持久化 + FTS5 搜索。前端发送消息 → Admin API → System ingress → Actor mailbox → agent loop → SSE 事件推回浏览器。测试 `test_conversation_events.py` 和 `test_web_chat_e2e.py` 验证了 SSE 事件映射。 |

**结论**: Web Chat 是唯一真正可用的端到端通道。用户在浏览器里可以和 Actor 对话，看到 agent 思考、tool call、tool result 的流式输出。但 IM (Telegram/Discord) 路由完全不存在。

---

### 步骤 5: 对话持续进行，上下文管理

| 系统边界 | 现状 |
|----------|------|
| **Rollover (自动摘要 + 上下文压缩)** | ✅ `SimpleLoopActor` 有 `ROLLOVER_THRESHOLD` (85% token 上限时触发)，自动生成摘要，创建新的 Agent 实例继承 Python session。 |
| **Prompt caching** | ✅ rollover 使用"追加法"，在 system prompt 末尾追加摘要 + "请继续完成任务"，利用 LLM 的 prompt caching。 |
| **Idle expiry** | ✅ `_idle_checker()` — Actor 在 1 小时无输入后自动停止。 |
| **Budget** | ✅ 循环检查 `budget.money.not_enough()`，超预算时自动回复用户并停止。 |
| **Python session 复用** | ✅ `ActorPythonSessionFactory` 管理 per-actor Python kernel，rollover 后继承。 |

**结论**: Actor 的长期运行管理（rollover / idle / budget / session）是完整的。但因为没有真实 integration 产生活跃消息流，这些机制只在测试和 Web Chat 中验证过。

---

## 第〇步: 用户如何把 yuubot 跑起来

这个场景在 checklist 里也有明确的定义：

> 提供一个交互式脚本，直接 `curl .... | bash` 执行完事儿。
> 用户需要准备: 域名(or localhost)、TLS 证书、端口配置、Master Key。
> 然后就应当可以访问 Admin 页面。登录后配置 LLM Provider → 配置 Actor → Web Chat 可用。
> 再去逐个配置插件。

| 系统边界 | 现状 |
|----------|------|
| **安装脚本** | ❌ 不存在。没有 `curl | bash` 安装脚本。 |
| **配置启动** | ✅ `ybot check` / `ybot daemon` / `ybot admin` / `ybot dev` CLI 命令完整，`config.example.yaml` 提供模板。 |
| **Admin 前端** | ✅ 已有功能页面，非纯 scaffold。`web/src/routes/` 下实现了: Chat (对话列表+对话界面)、Actors (列表+详情)、Characters (列表+详情)、Providers (LLM 供应商列表+详情)、Integrations (列表+详情)、Routes (Gateway 路由表)、Monitor (监控)、Settings (设置)。约 43 个 tsx/ts 文件。**但页面功能多为基础 CRUD 列表展示**，距离 checklist 要求的完整编辑器（如 Character 富文本编辑、Agent 技能展开配置、Actor 定时任务查看等）还有差距。 |
| **LLM Provider 管理** | ✅ API + 前端页面均存在 (`providers.tsx` + `providers.$id.tsx`)。 |
| **Actor 管理** | ✅ API + 前端页面均存在 (`actors.tsx` + `actors.$id.tsx`)。 |
| **Web Chat 对话** | ✅ API + 前端均存在。`chat.tsx` (dialog 列表+搜索+新建) + `chat.$dialogId.tsx` (消息历史+发送)。但**对话界面用的是轮询式 history reload**（发消息后 `getDialogMessages()` 拿回全部历史），没有接 SSE 流式推送。SSE 通道在 daemon 端已经完整（`ConversationManager.subscribe_events()`），前端未接入。 |
| **Integration 插件管理** | ✅ `ExternalPluginManager` 支持从目录/zip 安装外部插件，`admin/app.py` 提供插件 install/uninstall API。但没有任何真实可安装的插件。 |

---

## 跨切面: 数据目录、归档、持久化

| 系统边界 | 现状 |
|----------|------|
| **DirLayout** | ✅ `DataLayout` 定义了完整目录结构 (integrations/, yuubot/, workspace/, skills/, persistent-paths/)。 |
| **Archive export/import** | ✅ zip 导出导入完整，含 manifest.json。`test_archive_export_import.py` 验证。 |
| **符号链接持久化系统** | ❌ `design/checklist.md` 有详细规格（初始化时复制→创建符号链接→记录映射；导入时三种冲突策略；dry-run 预览）。**代码中完全不存在**。 |
| **EventBus** | ✅ `events.py` 中 async EventBus，用于 `ResourceChanged` 事件驱动刷新。 |

---

## 其他场景缺口汇总

不在上文的 Telegram Issue 场景中，但 checklist 列出的能力：

| 场景 | 缺口 |
|------|------|
| **OpenCode/Codex 集成** | ❌ 完全不存在。计划中有 `yext.opencode.client()`、`sync()`、`run()`、`list_agents()` 等 capability。 |
| **Web 工具 (Tavily search + web read)** | ❌ 完全不存在。计划中有 `web.search` 和 `web.read` capability。 |
| **Skills 管理** | ❌ `DataLayout.skills_dir` 预留了路径，但无任何 CRUD 或浏览/编辑 UI。`load_skills` 工具未实现。 |
| **Yuu Network / Bridge (ynet)** | ❌ 只有设计文档。计划中 agent node + resource node 的反向隧道网络，用于把外部机器接入 Actor 资源池。 |
| **Web PTY** | ❌ 不存在。允许用户通过浏览器终端连入远程机器调试。 |
| **Web FS** | ❌ 不存在。允许用户拖拽上传下载文件。 |
| **定时任务 (scheduled tools)** | ⚠️ `ScheduleTriggerMessage` 在代码中实际使用——当 `IncomingMessage` 到达时，`SimpleLoopActor.handle_message()` 将其包装为 `ScheduleTriggerMessage(agent_name=..., content=...)` 投递到 yuuagents Stage 的特定 agent。这是消息路由机制，**不是**时间触发的定时任务（cron）。`yb.schedule` facade 模块在 `FACADE_IMPORTS` 中声明，但 `run_schedule_tool()` 的实际实现委托给 yuuagents。时间驱动的定时任务能力取决于 yuuagents 的支持程度。 |
| **预设 Character/Agent/Actor** | ❌ 不存在。checklist 说"仅限初次启动时"通过插入数据库提供预设数据。 |
| **Admin 前端 (生产就绪)** | ⚠️ `web/` 目录有 React scaffold，但缺乏以下页面: LLM Provider 配置页面、Character 编辑器、Agent 配置页面、Actor 管理页面、Gateway 路由表、Skills 浏览器/编辑器、Plugin 管理页面、成本仪表盘。 |
| **IM Integrations (Telegram/Discord)** | ❌ 完全不存在。 |
| **项目管理 Integrations (Linear/GitHub/Notion/Lark/Google Calendar)** | ❌ 完全不存在。 |
| **W&B/SwanLab 集成** | ❌ 完全不存在。 |
| **成本仪表盘** | ❌ checklist 提到"yuubot需要自己编写成本仪表盘"。trace 数据有记录但无聚合展示。 |

---
