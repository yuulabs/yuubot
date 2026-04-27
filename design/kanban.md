# yuubot RFC2 重构 Kanban

本文是 `yuubot/design/plan.md` 的执行看板。目标是把 `yuubot` 从旧的 `yuuagents`/capability 运行方式迁移到 RFC2 形态：daemon 持有 `master_engine` 与 `group_engine` 两个长期 `yuuagents.Engine`，agent 只通过 `execute_python` 进入持久 Python session，并在 session 中 `import yb` 调用业务函数。

## 看板约定

- 状态：`[ ]` 未开始，`[~]` 进行中，`[x]` 完成，`[!]` 阻塞。
- 任务编号：`D-*` 删除阶段，`C-*` 核心重建阶段，`T-*` 工具还原阶段。
- 设计来源：最终架构以 `yuubot/design/plan.md` 为准；旧文档、旧测试和旧代码只保留需求、边界行为和第三方库知识。
- 验收优先级：先让 daemon/runtime 骨架可运行，再逐个恢复 `yb.*` 业务函数，避免旧 tool/capability 框架和新 Python API 并存。
- 概念边界：删除的是用户 Role / PermissionSet / 权限鉴权；保留 `Character` 作为 Master 多 agent 协作、delegate 目标和适用场景配置；Group 不做 delegate。

## 1. 删除阶段

目标：删掉会干扰新架构判断的旧 yuuagents runtime、旧 capability tool surface、旧 sandbox tool 与对应测试。删除前必须把仍然有效的需求、边界行为和第三方库知识沉淀到设计文档或后续任务中。

### 需求与知识保留

- [x] D-01 提取旧测试中的稳定需求：命令前缀、路由、会话续传、timeout、Master/Group scope、recorder 出入站、OneBot 渲染、记忆/网页/图片/日程可观察行为。
- [x] D-02 标记与 `plan.md` 冲突的旧需求：`call_cap_cli`/`read_cap_doc`/raw CLI、`sandbox_python`、`execute_bash`/`read_file`/`edit_file`、外部 yagents daemon/socket/db/docker runtime 均不是新 agent API；后续只作为 CLI/service 兼容或需求资料，不作为 agent 主协议。
- [x] D-03 建立第三方库知识清单：`yuuagents.Engine`、`AgentDefinition`、`Agent.steps()`、`PythonRuntime`、`PythonImport`、`JsonSessionState`、observer/billing、`yuullm.Message`、OneBot/NapCat、Tortoise ORM、httpx/aiohttp、scheduler/web/image 相关库。
- [x] D-04 为每个待恢复工具保留需求卡：IM、Memory、Web、Schedule、Vision、Image、Files、Ops、Delegate，记录输入/输出、Master/Group scope、审计、限流、缓存和测试入口。

#### 删除阶段保留的真实需求卡

- IM：发送真实 QQ 消息，支持当前 ctx 默认发送、Master 跨 ctx 发送、OneBot 段/多条消息/gap/poke、出站审查、mute、每分钟限流、落库；读取 recent/search/browse，支持 msg_id/时间范围/用户过滤；读取合并转发；联系人/群/成员/ctx 列表只对 Master facade 暴露；reaction 表情语义需保留。
- Memory：保存/召回/软删除/恢复/标签/配置；支持 public/private/ctx/user scope、tags、recall-terms、垃圾桶与 forget 周期；Master 可做全局整理，Group 只能处理当前群 scope；CLI 和 agent function 共享同一 service 语义。
- Web：搜索、阅读、下载；每任务搜索次数限制；搜索无果不应重复刷相似关键词；页面阅读要提正文/摘要/引用元数据；下载要进入受控缓存或 workspace。
- Schedule：创建/list/update/delete cron 任务；once 默认触发后禁用，recurring 才重复；绑定 ctx/user/agent；Master 可看全局，Group 只能看改当前群 ctx；长周期任务数量限制；到期后通过对应 Engine/session 或直接提醒执行。
- Vision/Image：图片描述中文文本、缓存与 refresh；支持 recorder media、本地路径、URL；图片库保存/搜索/删除/list tags；后续图片生成/编辑也走 service、缓存和安全策略。
- HHSH：保留缩写/黑话查询需求，可作为轻量 web/service 函数，不恢复旧 capability tool surface。
- Files/Ops：受控 workspace 文件读写、列表、补丁、命令/测试辅助、日志/健康检查；不恢复旧 unrestricted `execute_bash`/`read_file`/`edit_file` agent tool。
- Delegate：`yb.delegate()` 是 Master-only 稳定调用接口，不负责发现目标；当前可委派 Character 由 `yuubot` 根据 `master_delegate_targets` 和适用场景渲染进 Master system prompt，实际调用由 daemon host rules 校验；child agent 在 `master_engine` 中新建，拥有独立 history/runtime/Python session，生命周期结束后回收 Python session，最终结果返回 parent kernel；记录 parent/child trace；暴露 task_status/task_cancel/task_result。
- 边界行为：保留路由/命令前缀、Master/Group scope、`/ybot on`、recorder 入站落库/出站发送、OneBot 渲染、scheduler 数据模型、http client/model resolution 等不冲突需求；删除 auto/free mode 与直接断言旧 Flow/tool 状态的测试。

### 旧代码删除

- [x] D-05 删除旧 yuuagents v1/v0 集成层：`daemon/builder.py`、旧 `daemon/runtime_session.py`、旧 `daemon/agent_runner.py` 中依赖 `Basin`、`Flow`、`AgentConfig`、`AgentContext`、`RuntimeCapability` 的实现。
- [x] D-06 删除旧 summarizer fork 路径：移除 `daemon/summarizer.py` 中基于旧 `Agent`/`ToolManager` 的 fork runtime，后续统一通过 RFC2 child agent 或 service 摘要。
- [x] D-07 删除旧 capability-as-tool 入口：移除 `capabilities/` 旧 CLI/action/tool surface；真实需求已沉淀到本看板。
- [x] D-08 删除旧 sandbox tool：移除 `sandbox_python` 相关工具暴露和 `yuubot/sandbox/` 的运行时依赖；Python 执行统一交给 `yuuagents` kernel。
- [x] D-09 删除旧 Character tool/subagent 配置：清理 `prompt.py`、`characters/*` 中直接声明 `tools=[...]`、`subagents=[...]`、capability prompt 拼接的旧结构；保留 Character 本身并改为配置适用场景、facade、delegate targets。
- [x] D-10 删除旧外部 yagents daemon 配置生成路径：清理 `write_yagents_config()`、`build_yuuagents_config()` 中 socket/db/docker daemon 假设，保留 provider/model/trace 配置知识。
- [x] D-11 清理生成物：删除仓库内 `__pycache__/`、旧 trace/cache 测试残留和不再参与源码的临时文件。

### 旧测试处理

- [x] D-12 删除或冻结旧 runtime 测试：`test_agent_runner_*`、旧 `flows/test_agent_timeout_semantics.py`、旧 `flows/test_soft_timeout.py` 中直接断言旧 flow/tool 状态的用例。
- [x] D-13 删除或改写旧 capability contract/tool 调用测试：`test_react_pipeline.py`、旧 `test_capabilities_contract.py`、旧 `test_im_*` 中依赖 `CapabilityContext`/`execute()` 的用例。
- [x] D-14 保留不冲突的边界测试：routing 需求、recorder、OneBot、render、http client、model resolution、scheduler 数据模型等可作为新架构回归来源。
- [x] D-15 跑静态检索验收：`rg "Basin|Flow|AgentConfig|AgentContext|ToolManager|call_cap_cli|sandbox_python|CapabilityContext|capabilities.execute"` 不应再命中新运行时代码。

### 删除阶段出口

- [x] D-16 `yuubot` 中不再存在旧 yuuagents runtime 的导入链。
- [x] D-17 agent 可见能力不再通过旧 capability CLI tool 暴露。
- [x] D-18 新阶段所需需求和第三方库知识已有文档或任务卡承接。

## 2. 核心重建阶段

目标：接入 `yuuagents` RFC2 核心抽象骨架，但不实现具体工具行为。该阶段只建立稳定边界、目录、抽象、生命周期、会话和观测链路，为 `yb.*` 工具逐个还原做准备。

### 包结构骨架

- [x] C-01 创建 `src/yuubot/agent_fns/`：包含 `context.py`、`clients.py`、`im.py`、`mem.py`、`web.py`、`schedule.py`、`vision.py`、`image.py`、`files.py`、`ops.py`、`delegate.py` 和 `facades/`。
- [x] C-02 创建 `src/yuubot/services/`：包含 `im.py`、`mem.py`、`web.py`、`schedule.py`、`media.py`、`workspace.py`、`delegate.py`、`scope.py`。
- [x] C-03 定义共享模型：`AgentFnContext`、`Actor`、`BotKind`、`SessionScope`、`SessionState`、service 错误、审计事件、分页/引用/媒体等通用返回结构。
- [x] C-04 建立 facade 空壳：`facades/main.py`、`mem_curator.py`、`researcher.py`、`general.py`、`ops.py`、`coder.py`，只重导出占位函数和 docstring。

### yuuagents Engine 接入

- [x] C-05 在 daemon 启动阶段创建长期 `master_engine` 与 `group_engine`，分别注入 `YuuTraceObserver`、`YuubotRuntimeObserver`、billing sink 和 Python session factory。
- [x] C-06 在 daemon shutdown 分别调用 `await master_engine.close()` 与 `await group_engine.close()`，统一关闭 live agents、Python sessions、observers 和 billing。
- [x] C-07 实现 `BotProfile` 与 `Character` 新字段：Character 包含 `applicable_scenarios`、`enabled_bots`、`facade_modules`、`import_modules`、`expand_functions`、`startup_code`、`master_delegate_targets`、`max_turns`、`inactivity_timeout_s`。
- [x] C-08 实现 `AgentDefinition` 工厂：从 BotProfile、Character、LLM client、prompt 和 facade module 生成 `tools=("execute_python",)`、`PythonImport(..., alias="yb")`、`expand_functions=("yb.*",)`。
- [x] C-09 实现 `AgentRuntime` 工厂：按 Master/Group 构造 FullPythonSession 或 RestrictedPythonSession runtime，注入 cwd、env allowlist、extra env、sys_path、startup code 和 `JsonSessionState`。

### 会话与 step 驱动

- [x] C-10 重建 `RuntimeSession` 薄包装：只持有 `ya.Agent`、bot kind、character name、conversation id、task id、snapshot、状态和统计信息。
- [x] C-11 用 `Agent.steps(max_turns=1)` 驱动完整 turn：处理 `LlmStep`、`ToolStep`、`ErrorStep`，将最终无 tool call 的 assistant 文本交给 render/send。
- [x] C-11a 确认/实现 yuuagents 续跑 API：`agent.append_message(message: yuullm.Message)` 可追加消息并让 done/idle agent 继续 `steps()`；`close()`、fatal error 或 host rule 终止后不可续跑。
- [x] C-12 在 step 边界 drain 用户 signal queue：把新消息转换成 `yuullm.user(...)` 并追加到 live agent history。
- [x] C-13 实现 step 间 inactivity timeout：Python cell 超时先调用 `agent.interrupt_python()`；只有超时被杀、fatal error、daemon shutdown 或 Master child 生命周期结束时才 `agent.close()` 并记录状态。
- [x] C-14 接入 save/restore：conversation 保存 `engine.save_agent(agent)` snapshot，恢复时使用 `engine.restore_agent(definition, snapshot, runtime=...)`。
- [ ] C-14a 实现 context rollover：只在完整 turn 边界注入“context 快到上限，请总结”的 user message，让当前 agent 生成总结，再用新 prompt + 总结 + 必要近期消息原地替换 `agent.history`；不关闭 live agent，不重启 Python session。
- [x] C-15 统一旧 session、active flow、ping flow 为新的 conversation session 模型，并删除 auto/free mode。

### Daemon 边界与本地 API

- [x] C-16 保留并接入 dispatcher/routing，并删除用户 Role 鉴权路径：只保留 `/ybot on`；Master 开启后私聊可直接对话，Group 必须由 `/yllm` 或 at 显式驱动。
- [x] C-17 建立 daemon local API 骨架：供 kernel 侧 `DaemonClient` 调用 mem、delegate、workspace、scope 等服务，先返回 `NotImplemented` 或空结果。
- [x] C-18 建立 recorder local API client 骨架：供 `RecorderClient` 调用发送消息、读取媒体、读取消息等接口，先只定义协议和错误归一化。
- [x] C-19 实现 kernel token/session scope 骨架：token 绑定 `bot_kind`、`ctx_id`、`group_id`、`conversation_id`、`character_name`、`agent_id` 和过期时间。
- [x] C-20 建立 scope service 骨架：集中提供 Master global scope、Group current scope、workspace scope、Master delegate depth/budget 检查，不再提供 `require_permission()`。

### Prompt 与观测

- [x] C-21 重建 prompt 结构：只保留 bot base prompt、Character persona、QQ 场景、`execute_python` 使用、`import yb`、`SESSION_STATE`、`TASKS` 约定；Master 安全只保留发言审查并按 `master_delegate_targets` 插入可委派 Character，Group 安全说明 RestrictedPython 限制且不渲染 delegate；具体 API 文档由 import metadata 注入。
- [~] C-22 建立 observability 事件骨架：`agent.*`、`llm.*`、`tool.*`、`python.*`、`agent_fn.*`、`recorder.*`、`delegate.*`，统一携带 `bot_kind`/`character_name`/`ctx_id`/`conversation_id`/`agent_id`/`task_id`。
- [ ] C-23 更新 `scripts/conv.py` 读取路径：能从 trace 中查看 LLM、tool、Python cell、agent function 和最终 QQ 回复。
- [x] C-24 添加核心骨架测试：fake LLM + fake Engine/observer 验证创建 agent、step loop、final reply、timeout、restore 和 session state 注入。

### 核心重建阶段出口

- [x] C-25 `/yllm` 能创建 conversation、创建 live agent、跑通 fake LLM 最终文本回复。
- [x] C-26 `execute_python` 在测试中能 `import yb` 并读取 `SESSION_STATE`。
- [x] C-27 所有 `yb.*` 具体业务函数可以暂时是占位实现，但 Character facade、Master/Group scope、client、service 边界已稳定。

## 3. 工具还原阶段

目标：按 `plan.md` 中的 agent-facing Python API，把旧 capability 的真实行为逐个还原为 `yb.*` 函数和共享 domain services。每个工具先实现 service，再实现 agent function，再接 facade 和测试。

### 通用还原顺序

- [x] T-01 为目标工具整理需求卡：旧 contract、旧测试、真实使用示例、Master/Group scope、限流、审计、缓存和异常语义。
- [x] T-02 实现 domain service：服务层不依赖 LLM，不关心 prompt，只表达业务动作和稳定数据结构。
- [x] T-03 实现 daemon/recorder local API：kernel 侧只通过受控 client 调用宿主服务。
- [x] T-04 实现 `agent_fns` 函数：从 `AgentFnContext` 推导默认 bot kind、ctx、group、user 和 session scope，返回模型友好的 Python 值或 markdown。
- [x] T-05 接入 Character facade：按 Character 与 bot kind 重导出函数，并用 docstring 控制模型可见说明。
- [x] T-06 添加回归测试：service 单测、agent function 单测、Character facade 文档测试、必要的 flow 测试。

### IM 工具

- [x] T-10 还原 `yb.send_message()`：支持当前 ctx 默认发送、Master 跨 ctx 发送、OneBot 段渲染、出站审查、mute、限流和落库。
- [x] T-11 还原 `yb.recent_messages()`：默认读取当前 `ctx_id`，支持 limit、时间窗口、发送者过滤和结构化 segment。
- [x] T-12 还原 `yb.search_messages()`：支持关键词/用户/时间/ctx 过滤；Master 可跨 ctx 搜索，Group 只能搜索当前群 ctx。
- [x] T-13 还原 forward/contact/reaction 相关函数：读取合并转发、浏览联系人、发送 reaction 或可替代的 QQ 反馈。
- [~] T-14 更新 IM 相关 flow：Master 私聊开启后直接对话、Group 通过 `/yllm` 或 at 驱动、消息补充和最终回复落库。

### Memory 工具

- [x] T-20 还原 `yb.recall_memory()`：支持 query、scope、ctx/user/public/private 过滤和排序。
- [x] T-21 还原 `yb.save_memory()`：写入来源、actor、ctx、审计事件和去重提示。
- [x] T-22 还原整理/归档/恢复函数：供 `mem_curator` facade 使用，保留 trash/restore 行为。
- [x] T-23 统一 `ybot mem ...` 与 service：CLI 和 agent function 使用同一 scope 与数据语义。
- [~] T-24 更新 memory flow：public/private/ctx scope、删除恢复、curator 委派。

### Web 工具

- [x] T-30 还原 `yb.web_search()`：接入搜索 provider、限流、结果结构化、引用元数据和错误归一化。
- [x] T-31 还原 `yb.read_page()`：页面下载、正文提取、摘要、引用片段和 blocklist。
- [x] T-32 还原下载/文件缓存函数：支持 URL 下载、媒体缓存、workspace 路径约束。
- [~] T-33 统一 `ybot web ...` 与 service：登录、cookie、blocklist 和 provider 配置复用。
- [~] T-34 更新 web flow：搜索-阅读-引用整理，覆盖 provider 错误和限流。

### Schedule 工具

- [x] T-40 还原 `yb.create_schedule()`：创建提醒、绑定 ctx/user、session scope 和可见内容。
- [x] T-41 还原 `yb.list_schedules()`、`yb.cancel_schedule()`、`yb.update_schedule()`。
- [x] T-42 接入 daemon scheduler：到期任务通过新 RuntimeSession/Engine 创建 agent 或发送提醒。
- [x] T-43 统一 `ybot schedule ...` 与 service。
- [~] T-44 更新 schedule flow：创建、取消、重启恢复、Master/Group scope 和提醒发送。

### Vision 与 Image 工具

- [x] T-50 还原 `yb.describe_image()`：读取 recorder media、缓存 key、multimodal LLM、refresh 标记和结果引用。
- [x] T-51 还原 OCR/媒体元数据函数：支持 QQ 图片、文件、本地路径和 URL。
- [~] T-52 还原图片生成/编辑函数：接入 image provider、缓存、发送辅助和安全策略。
- [ ] T-53 统一 `ybot img/vision ...` 与 service。
- [ ] T-54 更新多模态 flow：图片理解、缓存命中、图片回复和错误降级。

### Files、Ops 与 Workspace 工具

- [x] T-60 还原 workspace 文件读写：限制在 `workspace_root`，支持读、写、列表、补丁和报告。
- [x] T-61 还原受控测试/命令辅助：Master 可使用运维/代码辅助，Group 只能使用 RestrictedPython 允许的命令、目录和超时。
- [~] T-62 还原 ops 日志/健康检查：daemon/recorder/NapCat 状态、最近日志、部署信息。
- [ ] T-63 还原 `ybot docker shell` 等运维入口与 service 的共享逻辑。
- [ ] T-64 更新 coder/ops flow：文件补丁、测试摘要、日志排查、Group scope 拒绝。

### Master Delegate 与长任务工具

- [~] T-70 还原 Master-only `yb.delegate()`：通过 `yuuagents.kernel.delegate` 或 daemon RPC 把目标 Character 名称和 task prompt 交给 host；Group facade 不导出该函数。
- [~] T-71 实现 `DelegateRules`：限制目标 Character、enabled bots、适用场景、深度、并发、预算、timeout 和 child 可见能力；所有限制以 daemon 校验为准，且只允许 Master parent。
- [ ] T-72 记录 parent/child trace：observer 事件能按 task tree 展开。
- [x] T-73 还原 `yb.task_status()`、`yb.task_cancel()`、`yb.task_result()`：管理 `TASKS` 中长期 asyncio task 的用户可见状态。
- [ ] T-74 更新 Master delegate flow：researcher/coder/ops 委派、后台任务、取消、timeout 和结果回传；Group 验证无 delegate 入口。
- [~] T-75 实现 child agent 生命周期：在 `master_engine` 新建独立 child `AgentDefinition`/history/runtime/Python session，最终文本、结构化结果或错误摘要返回 parent kernel，child 结束后回收 Python session。
- [~] T-76 在 Master system prompt 中渲染可委派 Character：展示名称、适用场景、用途和约束；Group prompt 不渲染 delegate；`yb.delegate` docstring 保持通用，执行时 daemon 重新校验 host rules。

### Character facade 与最终集成

- [x] T-80 完成 `facades/main.py`：日常聊天、IM、Memory、Web、Vision。
- [x] T-81 完成 `facades/mem_curator.py`：记忆整理、去重、归档、恢复、上下文审阅。
- [x] T-82 完成 `facades/researcher.py`：网页研究、资料汇总、引用整理、报告草稿。
- [~] T-83 完成 `facades/general.py`：通用任务、消息/记忆/网页/日程；委派函数仅在 Master 可见。
- [~] T-84 完成 `facades/ops.py`：运维、日程、日志、部署健康检查、受控 workspace。
- [x] T-85 完成 `facades/coder.py`：文件、补丁、git、测试、代码报告、委派。
- [x] T-86 验证 prompt/tool 文档同源：`expand_functions` 能按 Character 与 bot kind 展示正确函数签名和 docstring。
- [x] T-87 跑最终回归：`cd yuubot && uv run pytest tests/test_agent_fns.py`、关键 flow tests、`uv run ty check`。

### 工具还原阶段出口

- [~] T-88 `/yllm`、续传、timeout、restore、最终回复、工具调用和 trace 在真实 daemon 中闭环。
- [~] T-89 `yb.recent_messages()`、`yb.recall_memory()`、`yb.web_search()`、`yb.describe_image()`、Master-only `yb.delegate()` 等核心函数通过集成测试。
- [~] T-90 CLI、daemon command handler、agent functions 对同一业务操作使用同一 service 和 Master/Group scope 语义。
