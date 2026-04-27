# yuubot 基于 yuuagents RFC2 的目标架构

本文聚焦最终技术形态：`yuubot` 是什么、为什么采用这种形态，以及各模块如何协作落地。

## 1. 架构目标

`yuubot` 的目标形态是一个以 QQ / OneBot 为边界、以 `yuuagents` RFC2 为 agent runtime 的机器人系统。

核心目标：

1. `Recorder` 专注 QQ 边界适配、消息落库、媒体落库和出站发送。
2. `Daemon` 专注路由、会话、Master Bot / Group Bot 编排、业务服务和 agent 生命周期。
3. `yuuagents.Engine` 拆成 `master_engine` 与 `group_engine` 两个长期运行实例，分别承载 Master 私聊与群聊。
4. 每个 live agent 拥有一个持久 Python session，通过 `execute_python` 调用业务能力。
5. 业务能力以 importable Python functions 暴露给 agent，以普通 Python 表达组合、批处理、等待、并发和后台任务。
6. 不再保留 Role / PermissionSet / 用户等级鉴权概念；运行边界只区分 Master 私聊与群聊。
7. Master Bot 使用完整 Python session 与全局服务视图，可以查看联系人、群、跨上下文消息和全局配置。
8. Group Bot 使用 RestrictedPython session 与群作用域服务视图，每个群聊完全隔离，无法访问联系人列表或其他群。
9. `Character` 仍是多 agent 协作的核心：它描述人格、适用场景、可见 facade、Master delegate 目标和模型/预算偏好。
10. Master Bot 支持 delegate 与多 agent 协作；Group Bot 不暴露 delegate，也不创建 child agent。
11. 安全与隔离依赖架构拆分、session backend 和服务 API 面，而不是依赖 prompt 按上下文过滤。
12. trace、日志、conversation history 可以从 `bot_kind`、`character_name`、`ctx_id`、`conversation_id`、`agent_id`、`task_id` 贯通排查。
13. 大幅减少代码量、提升可维护性。

## 2. 总体结构

最终结构：

```text
NapCat / OneBot
  -> Recorder
      -> message store
      -> media store
      -> recorder local API
      -> daemon relay
  -> Daemon
      -> dispatcher / routing
      -> domain services
      -> Master Bot
          -> master conversation manager
          -> master_engine
              -> Character
              -> AgentDefinition
              -> Agent.steps()
              -> execute_python
              -> FullPythonSession
                  -> import yb
                  -> character-specific master facade
                  -> global local service clients
      -> Group Bot
          -> per-group conversation manager
          -> group_engine
              -> Character
              -> AgentDefinition
              -> Agent.steps()
              -> execute_python
              -> RestrictedPythonSession
                  -> import yb
                  -> character-specific group facade
                  -> current-group local service clients
```

职责划分：

1. `Recorder` 是 QQ IO 边界，处理 OneBot event、消息与媒体持久化、出站消息安全检查、群聊限流、mute、NapCat HTTP 代理。
2. `Daemon` 是决策边界，只做 Master 私聊与群聊的路由分流、session 生命周期、调度器和 agent orchestration。
3. `Master Bot` 服务 Master 私聊，绑定 `master_engine`、完整 Python session 和全局 service client。
4. `Group Bot` 服务群聊，绑定 `group_engine`、RestrictedPython session 和按群隔离的 service client。
5. `Character` 是 agent 协作边界，描述某个 agent 适合处理什么场景、使用哪个 prompt/facade、可 delegate 给谁。
6. `yuuagents` 是 agent runtime 边界，处理 LLM/tool loop、step streaming、Python session 生命周期、工具结果归集和 observer 事件。
7. `agent_fns` 是 agent 编程边界，给 kernel 中的 Python 代码提供稳定业务 API。
8. `domain services` 是业务实现边界，CLI、agent functions、daemon command handler 共享同一套实现，但对 Master 与 Group 暴露不同服务面。

## 3. Daemon 运行时

Daemon 启动时创建两个长期存在的 `yuuagents.Engine`。不要创建“一个 Bot 再按上下文过滤能力”的结构：

```python
master_engine = ya.Engine(
    observers=[
        ya.YuuTraceObserver(...),
        YuubotRuntimeObserver(...),
    ],
    billing=YuubotBillingSink(...),
    python_session_factory=FullPythonSessionFactory(...),
)

group_engine = ya.Engine(
    observers=[
        ya.YuuTraceObserver(...),
        YuubotRuntimeObserver(...),
    ],
    billing=YuubotBillingSink(...),
    python_session_factory=RestrictedPythonSessionFactory(...),
)
```

Engine 的生命周期绑定 daemon 进程：

1. daemon startup 创建 `master_engine`、`group_engine`、domain services、command tree、conversation managers、scheduler、relay client。
2. daemon shutdown 分别调用 `await master_engine.close()` 与 `await group_engine.close()`，统一关闭 live agents、Python sessions、observers 和 billing sink。
3. Master 私聊 conversation 只在 `master_engine` 中创建或恢复 `ya.Agent`。
4. 群聊 conversation 只在 `group_engine` 中创建或恢复 `ya.Agent`，并按 `group_id` 分配独立 session scope、workspace 和服务 token。
5. `RuntimeSession` 是 `yuubot` 对 `ya.Agent` 的薄包装，负责把 step 映射到 QQ 可观察行为与 conversation 状态。

创建 live agent 的形状。`BotProfile` 决定 Engine / Python session / scope，`Character` 决定人格、适用场景、facade 和 delegate 目标：

```python
bot = select_bot_profile(route)  # "master" or "group"
character = select_character(route, bot)  # main / researcher / coder / ...
engine = master_engine if bot.kind == "master" else group_engine

definition = ya.AgentDefinition(
    name=character.name,
    llm=llm_client,
    system_prompt=render_prompt(bot, character, conversation),
    tools=("execute_python",),
    import_modules=(
        character.import_modules_for(bot.kind)
        or [ya.PythonImport(character.facade_module_for(bot.kind), alias="yb")]
    ),
    expand_functions=character.expand_functions_for(bot.kind),
)

runtime = ya.AgentRuntime(
    python=ya.PythonRuntimeOverride(
        python=bot.python.executable,
        cwd=workspace.workdir,
        inherit_envs=bot.python.inherit_envs,
        env_allowlist=bot.python.env_allowlist,
        extra_envs=kernel_env(conversation),
        sys_path=kernel_sys_path(config),
        startup_code=character.startup_code,
        state=ya.JsonSessionState(session_state(conversation, bot, character)),
    ),
)

agent = engine.create_agent(definition, initial_messages, runtime=runtime)
```

为什么这样设计：

1. 两个 `Engine` 成为运行时资源所有者，daemon 只需要管理业务层生命周期和路由。
2. Master 与 Group 的 session backend、scope token、base prompt 和 service client 在创建时已分离。
3. `PythonRuntime` 在创建 live agent 时解析，保证同一会话内 Python 环境稳定。
4. `Character` 提供可审查的 prompt、facade、`import_modules`、`expand_functions`、适用场景和 delegate 目标。
5. `PythonImport(..., alias="yb")` 给模型一个短而一致的业务 API 入口，实际可导入能力由 bot kind + character facade 决定。
6. `JsonSessionState` 把 QQ 上下文、bot kind、character name、session scope 和本地服务地址注入 kernel，函数签名保持业务化。
7. Group Bot 的限制由 `RestrictedPythonSession` 和群作用域 service client 实现，不靠 prompt 反复声明“不能做什么”。

## 4. Agent step 驱动

`RuntimeSession` 通过 `Agent.steps()` 驱动 live agent：

```python
async for step in agent.steps(max_turns=max_turns):
    match step:
        case ya.LlmStep():
            await handle_llm_step(step)
        case ya.ToolStep():
            await handle_tool_step(step)
        case ya.ErrorStep():
            await handle_error_step(step)
```

step 语义：

1. `LlmStep` 表示一次 LLM response 已进入 `agent.history`。
2. `ToolStep` 表示一次 tool call 已完成，tool result 已进入 `agent.history`。
3. `ErrorStep` 表示 live agent 进入错误终止状态。
4. 一个含多个 tool calls 的 LLM response 产出一个 `LlmStep` 和多个顺序 `ToolStep`。
5. 最终 `LlmStep` 的 `tool_calls` 为空时，daemon 将其文本渲染为 QQ 回复。

多轮会话语义：

1. 对 `yuubot` 来说，最终无 tool call 的 assistant 回复只表示“当前 turn 完成并进入 idle”，不应强制结束整个 conversation live agent。
2. `yuuagents` 需要提供公开续跑入口 `agent.append_message(message: yuullm.Message)`：向未关闭、非错误的 live agent 追加任意 `yuullm.Message`，并允许后续继续 `steps()`。
3. 该续跑入口可以清除“正常完成当前 turn”的 done/idle 标记，但不能恢复已 `close()`、fatal error 或 host rule 终止的 agent。
4. Master 与 Group 都保留 conversation 接续语义；接续发生在原 live agent 上，不通过关闭并重建 agent 表达。

Daemon 的附加职责：

1. 将 `LlmStep.message`、`ToolStep.output`、usage、cost、duration 写入 conversation 与 trace。
2. 将最终 assistant 文本、图片和结构化展示结果转换为 OneBot 发送结构。
3. 在每个 step 边界 drain 用户 signal queue，把新到消息追加为 `yuullm.user(...)`。
4. 用 `asyncio.timeout(inactivity_timeout_s)` 包裹下一次 step 等待，保持“长时间无进展”语义。
5. Python cell 超时或人工中断时调用 `agent.interrupt_python()`，并把状态写入 trace。

中断与 rollover 语义：

1. `Agent.steps()` 不承担上下文压缩、agent 级中断或自动 rollover；这些属于 `yuubot` 的 driver 层职责。
2. `steps(max_turns=1)` 是推荐 checkpoint：完成一次 LLM response 以及该 response 产生的全部 tool calls 后返回。
3. 不在已有 `LlmStep.tool_calls` 但对应 `ToolStep` 尚未全部写入 history 时压缩或替换 history，避免产生非法 tool-call 消息序列。
4. 发现下一轮可能超过 token 上限时，daemon 在完整 turn 边界向当前 agent 注入一条系统生成的 user message，例如“你的 context 已经快到上限，请总结当前对话、待办、关键事实、已使用工具结果和后续注意事项。”
5. 当前 agent 生成总结后，daemon 用新 prompt、总结消息和必要的少量近期消息直接替换现有 `agent.history`；live agent 与 Python session 不关闭。
6. `yuuagents` 需要提供公开 history rewrite 入口，例如 `agent.replace_history(messages)`，用于在保留 live agent / Python session 的情况下原子替换 history。
7. `agent.interrupt_python()` 只用于正在执行的 Python cell timeout 或人工停止；`agent.close()` 只用于超时被杀、fatal error、daemon shutdown 或 Master child agent 生命周期结束。

恢复语义：

1. `engine.save_agent(agent)` 持久化 conversation history。
2. `engine.restore_agent(definition, snapshot, runtime=runtime)` 恢复 live agent。
3. 恢复后的下一次 `execute_python` 创建新 kernel，并通过 session state、prompt 和 history 重建所需上下文。

## 5. Python session

每个 live agent 拥有一个独立 Python session，但 Master Bot 与 Group Bot 使用不同实现：

```text
master private conversation -> master_engine -> agent m1 -> FullPythonSession p1
group g1 conversation      -> group_engine  -> agent g1 -> RestrictedPythonSession p2
group g2 conversation      -> group_engine  -> agent g2 -> RestrictedPythonSession p3
```

session 属性：

1. 首次调用 `execute_python` 时懒启动。
2. 同一 session 内变量、imports、`TASKS`、对象引用跨 cell 保留。
3. 同一 session 内 cell 顺序执行，不同 live agent 的 session 可并发运行。
4. `SESSION_STATE` 和 `yuuagents.kernel.get_session_state()` 提供 JSON 上下文。
5. `TASKS = {}` 是长期 asyncio task 的约定存放位置。
6. `display(...)`、markdown、JSON、图片等 Jupyter 可见输出转换为 `yuullm.ToolOutput`。
7. Master session 使用完整 Python 能力与全局 service client。
8. Group session 使用 RestrictedPython backend、按群 workspace、按群 local API token 和受限 import/builtin/network/file 策略。
9. Master root agent 的 Python session 随 conversation 存续；Master delegate child agent 属于非 Root agent，其 Python session 在 child 生命周期结束后立即回收。
10. Group Bot 不创建 delegate child agent，因此不存在 Group 非 Root Python session。

`SESSION_STATE` 的推荐字段：

| 字段 | 含义 |
| --- | --- |
| `bot_kind` | `master` / `group` |
| `ctx_id` | 统一上下文 ID |
| `chat_type` | `private` / `group` 等聊天类型 |
| `group_id` | 群号，私聊为空 |
| `user_id` | 当前触发用户 |
| `conversation_id` | daemon conversation ID |
| `character_name` | 当前 Character 名 |
| `task_id` | 当前 agent task ID |
| `bot_id` | 机器人 QQ 号 |
| `bot_name` | 机器人显示名 |
| `workspace_root` | agent 可用工作目录 |
| `recorder_base_url` | recorder local API |
| `daemon_base_url` | daemon local API |
| `session_scope` | `global` 或 `group:{group_id}` |
| `delegate_depth` | 委派深度 |

## 6. Agent-facing Python API

Agent 看到的业务 API 是 `import yb` 后的一组普通 Python functions。

设计原则：

1. 函数名描述业务动作，参数使用业务语义。
2. 函数默认从 `SESSION_STATE` 推导 `bot_kind`、`ctx_id`、`group_id`、`user_id` 和工作目录。
3. Master facade 可以提供全局操作与显式 `ctx_id` / `group_id` 参数；Group facade 不暴露跨群、联系人列表或全局配置函数。
4. 函数返回普通 Python 值、dataclass / `msgspec.Struct`、字符串、markdown 或 multimodal content 兼容结构。
5. 模块 docstring 和函数 docstring 是模型可见说明，也是人类维护文档。
6. 限流、审计、出站审查和 session scope 约束在函数或其调用的 domain service 内执行。

调用示例：

```python
import asyncio
import yb

await yb.send_message("收到，我先查一下。")

recent = await yb.recent_messages(limit=30)
memories = await yb.recall_memory("跑团偏好", limit=5)
pages = await yb.web_search("OpenAI structured outputs 最新说明", limit=5)
```

Master Bot 中还可以使用 delegate 做多 agent 协作：

```python
import asyncio
import yb

TASKS["research"] = asyncio.create_task(
    yb.delegate("researcher", "阅读搜索结果并整理要点")
)
```

Group Bot 不导出 `yb.delegate()`；群聊中的长任务只使用当前 Root agent 的 `TASKS`。

为什么使用 Python functions：

1. 模型可以在 Python 中筛选、聚合、重试、分页、并发和缓存中间结果。
2. 长任务和后台任务以 `asyncio` / `TASKS` 表达，语义与 Python runtime 一致。
3. 函数签名比 CLI 字符串更稳定，类型和 docstring 更容易测试。
4. `yuuagents` 负责 kernel 生命周期和函数元数据注入，`yuubot` 专注业务 API。
5. Master/Group 差异通过 session backend、scope token 和 Character facade 表达，不通过同一套 API 运行时鉴权。

## 7. agent_fns 包结构

目标包结构：

```text
src/yuubot/agent_fns/
  __init__.py
  context.py
  clients.py
  im.py
  mem.py
  web.py
  schedule.py
  vision.py
  image.py
  files.py
  ops.py
  delegate.py
  facades/
    __init__.py
    main.py
    mem_curator.py
    researcher.py
    general.py
    ops.py
    coder.py
```

模块职责：

1. `context.py` 读取 `SESSION_STATE`，构造 `AgentFnContext`，提供 `current_context()`、`require_master_scope()`、`require_group_scope()`、`require_current_group()`。
2. `clients.py` 封装 recorder / daemon local API client、session scope token、重试、超时和错误归一化。
3. `im.py` 提供消息发送、最近消息、消息搜索、forward 阅读、联系人浏览、reaction 等函数；联系人与跨上下文函数只从 master facade 导出。
4. `mem.py` 提供记忆检索、保存、整理、恢复、归档、配置等函数。
5. `web.py` 提供搜索、阅读、下载、页面摘要和引用提取。
6. `schedule.py` 提供日程创建、查询、取消、提醒内容更新。
7. `vision.py` 提供图片描述、OCR、媒体元数据读取。
8. `image.py` 提供图片生成、编辑、缓存和发送辅助。
9. `files.py` 提供 workspace 内文件读写、补丁、测试命令和报告生成辅助。
10. `ops.py` 提供部署、日志、健康检查和受控 shell 辅助。
11. `delegate.py` 封装 `yuuagents.kernel.delegate` 或 daemon delegate RPC。
12. `facades/*` 是 Character 可见 API；同一个 facade 在 Master/Group 下通过不同 session scope 与 service client 自然得到不同能力，必要时 Character 也可以按 bot kind 选择不同 facade。

Facade 示例：

```python
"""Group chat helper functions scoped to the current QQ group."""

from yuubot.agent_fns.im import recent_messages, search_messages, send_message
from yuubot.agent_fns.mem import recall_memory, save_memory
from yuubot.agent_fns.web import read_page, web_search
from yuubot.agent_fns.vision import describe_image

__all__ = [
    "send_message",
    "recent_messages",
    "search_messages",
    "recall_memory",
    "save_memory",
    "web_search",
    "read_page",
    "describe_image",
]
```

Facade 的作用：

1. 让 `PythonImportDoc` 按 Character 的 `expand_functions` 展示 module-level public functions。
2. 让 prompt 中的能力说明与实际可导入函数保持一致。
3. 让 Master/Group 与 Character 差异通过 Python module 组织表达，配置上通过 `character.import_modules_for(bot_kind)` / `character.expand_functions_for(bot_kind)` 审查。
4. 让隔离边界由 Engine、session backend 和 service client 承担，模型可见性仅用于降低提示词噪声。

## 8. Domain service 层

`yuubot` 的业务实现沉淀在 daemon / recorder 可复用 service 中，agent functions 是 kernel 侧 client facade。

建议结构：

```text
src/yuubot/services/
  im.py
  mem.py
  web.py
  schedule.py
  media.py
  workspace.py
  delegate.py
  scope.py
```

服务原则：

1. CLI、daemon command handler、agent functions 共享同一套 service 语义。
2. kernel 进程通过 local HTTP / RPC client 调用 daemon 或 recorder service。
3. recorder 侧继续负责 OneBot 出站、消息落库、媒体下载、限流和 mute。
4. daemon 侧只维护 Master scope、Group scope、memory scope、delegate rules、workspace scope；不维护用户 Role 或 PermissionSet。
5. service 返回稳定数据结构，agent functions 再转换为模型友好的 Python 值或 markdown。

典型调用链：

```text
yb.send_message(...)
  -> AgentFnContext
  -> RecorderClient.send_message(...)
  -> recorder.api
  -> OneBot send
  -> message store
```

```text
yb.recall_memory(...)
  -> AgentFnContext
  -> DaemonClient.mem_recall(...)
  -> memory service
  -> scope service
  -> structured memory records
```

## 9. Bot 与 Character 定义

顶层运行边界只保留两个 Bot：Master Bot 与 Group Bot。`Character` 仍然保留，并作为多 agent 协作、delegate 目标和任务分工的核心配置。

推荐字段：

```python
BotKind = Literal["master", "group"]

@define(frozen=True)
class BotProfile:
    kind: BotKind
    engine_name: str
    python_backend: Literal["full", "restricted"]
    base_prompt: str
    safety_prompt: str
    python: PythonRuntimeConfig


@define(frozen=True)
class Character:
    name: str
    description: str
    applicable_scenarios: tuple[str, ...]
    enabled_bots: frozenset[BotKind]
    llm: str
    system_prompt: str
    facade_modules: Mapping[BotKind, str]
    import_modules: Mapping[BotKind, tuple[ya.PythonImport | str, ...]] = {}
    expand_functions: Mapping[BotKind, tuple[str, ...]] = {}
    startup_code: str = ""
    master_delegate_targets: tuple[str, ...] = ()
    max_turns: int | None = None
    inactivity_timeout_s: float | None = None
```

Bot 表：

| Bot | Engine | Python session | service scope | 主要用途 |
| --- | --- | --- | --- | --- |
| `master` | `master_engine` | FullPythonSession | global | Master 私聊、全局消息/联系人/群管理、记忆整理、网页、图片、运维 |
| `group` | `group_engine` | RestrictedPythonSession | current group | 当前群聊、当前群消息/记忆/网页/图片/日程、受限长任务 |

Character 表：

| Character | enabled bots | 适用场景 |
| --- | --- | --- |
| `main` | `master`, `group` | 日常聊天、消息浏览、记忆检索、网页阅读、图片理解 |
| `mem_curator` | `master`, optionally `group` | 记忆整理、去重、归档、恢复、上下文审阅 |
| `researcher` | `master`, `group` | 网页研究、资料汇总、引用整理、报告草稿 |
| `general` | `master`, `group` | 通用任务、消息与记忆、网页、日程；仅 Master 可轻量委派 |
| `ops` | `master` | 运维、日程、日志、部署健康检查、受控 workspace 操作 |
| `coder` | `master` | 文件、补丁、git、测试、代码报告、委派 |

所有 Character 的 LLM tool surface 保持一致：

```python
tools=("execute_python",)
```

Bot 差异由以下维度表达：

1. `master_engine` / `group_engine`。
2. FullPythonSession / RestrictedPythonSession。
3. 全局 service client / 当前群 service client。
4. `SESSION_STATE.bot_kind` / `SESSION_STATE.session_scope`。
5. base prompt 与安全条目。

Character 差异由以下维度表达：

1. `applicable_scenarios`，用于路由选择、delegate 目标说明和 prompt 展示。
2. persona / `system_prompt`。
3. `facade_modules[bot_kind]` / `import_modules[bot_kind]`。
4. `expand_functions[bot_kind]`。
5. `master_delegate_targets`，只在 Master Bot 中生效。
6. model alias、预算、timeout、startup code。

Delegate 可见性规则：

1. `AgentDefinition` 不包含可委派目标列表，`yb.delegate(...)` 也只是稳定调用接口，不负责发现“可以调用谁”。
2. 只有 Master Bot 渲染 delegate 列表，并根据当前 Character 的 `master_delegate_targets`、depth 和预算插入可委派 Character 的名称、用途与适用场景。
3. Group Bot 不渲染 delegate 列表，不导出 `yb.delegate()`，daemon 也不会为 Group 创建 child agent。
4. system prompt 中的 delegate 列表只用于模型引导；daemon 在实际执行 `yb.delegate(...)` 时必须重新校验目标 Character 是否启用于 Master 与当前 scope。
5. `execute_python` 中展开的 `yb.delegate` docstring 保持通用，不动态承载可委派列表，避免与 host rules 分叉。

## 10. Master / Group 边界与上下文

系统不再有 Role / PermissionSet / 用户等级鉴权模型。边界规则如下：

1. 配置中只有 Master QQ 号列表；只有这些用户的私聊消息会进入 Master Bot。
2. 群聊消息进入 Group Bot，并按 `group_id` / `ctx_id` 创建完全隔离的 conversation、workspace、session state 和 service token。
3. 普通用户 ID 只作为消息作者、审计 actor 和回复目标，不参与 Role 判定。
4. command handler、agent functions 和 services 不再调用 `require_permission()`；它们只依据 `session_scope` 决定 Master 全局面或当前群面。
5. 只保留 `/ybot on` 这一开启语义；删除旧 auto/free mode 及其路由分支。

Master Bot 边界：

1. 使用 `master_engine` 和 FullPythonSession。
2. 使用全局 service client，可读取联系人、群列表、跨上下文消息、全局记忆、运维状态。
3. Master 场景下 `/ybot on` 后即可直接私聊对话，不需要 `/yllm` 或 at 触发。
4. Master Bot 可以使用 delegate 做多 agent 协作。
5. prompt 的安全条目只保留发言审查、隐私判断和高影响操作确认，不再强调能力限制。

Group Bot 边界：

1. 使用 `group_engine` 和 RestrictedPythonSession。
2. service token 只绑定当前 `group_id` / `ctx_id`；消息、记忆、日程、workspace 默认且只能落在当前群 scope。
3. group facade 不导出联系人列表、群列表、跨群消息搜索、全局配置、完整文件系统或 shell 入口。
4. Group Bot 不使用 delegate，不创建 child agent；所有群聊任务都由当前群 Root agent 处理。
5. Group 场景下即使 `/ybot on` 已开启，也必须由 `/yllm` 或 at 显式驱动对话；普通群消息只落库，不自动续聊。
6. prompt 的安全条目需要说明 RestrictedPython 执行器限制，例如受限 builtins/imports、受限文件访问、受限网络/子进程能力和超时。
7. 每次高影响操作写审计事件，审计事件包含 `bot_kind`、`ctx_id`、`group_id`、`user_id`、`agent_id`、函数名和参数摘要。

## 11. Master Delegation 与长任务

Delegation 只属于 Master Bot，用于多 agent 协作。Group Bot 不导出 `yb.delegate()`，也不创建 child agent。

Master 委派在 Python 中表现为普通 async function：

```python
result = await yb.delegate(
    agent="researcher",
    task="阅读这些网页并整理支持与反对观点。",
    timeout_s=120,
)
```

并发委派和长任务使用 Python 调度：

```python
import asyncio
import yb

TASKS["topic_a"] = asyncio.create_task(
    yb.delegate("researcher", "研究 topic A")
)
TASKS["topic_b"] = asyncio.create_task(
    yb.delegate("researcher", "研究 topic B")
)

done, pending = await asyncio.wait(
    [TASKS["topic_a"], TASKS["topic_b"]],
    timeout=60,
)
```

Host 侧语义：

1. `yb.delegate(...)` 通过 `yuuagents.kernel.delegate` 或 daemon local RPC 回到 host，输入是一段给目标 agent 的初始 task prompt。
2. daemon 要求 parent 必须是 Master Bot，并根据 session scope、深度、并发、预算、timeout 和目标 Character 校验；校验结果以 host 为准，不信任模型传参或 prompt 文案。
3. host 在 `master_engine` 中创建 child `Agent`。
4. child 拥有独立的 `AgentDefinition`、history、runtime 和 Python session。
5. child 的初始 history 由目标 Character system prompt、applicable scenarios、parent/ctx 元数据和 `task` prompt 组成；child session 中的变量、imports 和 `TASKS` 与 parent 完全隔离。
6. daemon 驱动 child `Agent.steps(max_turns=...)`，直到最终无 tool call 的 assistant 文本、timeout、error 或 scope 终止。
7. child agent 的最终文本、结构化结果或错误摘要序列化为普通 Python 值返回 caller kernel。
8. child agent 属于非 Root agent；无论成功、错误还是取消，生命周期结束后都关闭并回收其 Python session。
9. observer 事件记录 parent / child 关系，trace 可按 task tree 展开。

长任务语义：

1. agent 将长期 asyncio task 存入 `TASKS`。
2. 后续 cell 通过 `TASKS[name].done()`、`await TASKS[name]`、`TASKS[name].cancel()` 管理任务。
3. Master 中需要用户可见状态时，`yb.task_status()`、`yb.task_cancel()`、`yb.task_result()` 提供薄封装。
4. Group 中可使用当前 Root agent 的 `TASKS` 管理长任务，但不通过 delegate 创建 child agent。

## 12. 视觉、多模态与媒体

媒体能力以 `yb` 函数暴露：

```python
description = await yb.describe_image(media_id="...", refresh=False)
```

执行方式：

1. `vision` service 从 recorder media store 解析 QQ 图片、文件、本地路径或 URL。
2. 缓存 key 由媒体内容 hash、模型名、prompt version 和 refresh 参数组成。
3. 视觉描述使用 multimodal LLM；复杂分析可创建 `tools=()` 的 `vision` agent 并遍历 `steps(max_turns=1)`。
4. 结果返回文本、引用媒体信息、缓存命中状态和可选 markdown。
5. Python rich output 中的图片、markdown、JSON 由 `PythonExecTool` 转成 `yuullm.ToolOutput`，daemon 再渲染为 OneBot 消息段。

## 13. 配置与部署

`config.yaml` 负责表达 `yuubot` 运行所需配置：

1. LLM provider、model alias、budget 和 Character 默认模型。
2. Master QQ 号列表、recorder / daemon 端口、base URL、local session token 配置。
3. Master FullPythonSession 与 Group RestrictedPythonSession 的 interpreter、env allowlist、extra env、sys path、startup code。
4. `master` / `group` 两个 Bot 的 base prompt、安全条目、session backend 和 timeout。
5. 每个 Character 的适用场景、启用 Bot、`facade_modules`、`import_modules`、`expand_functions`、Master delegate targets 和 timeout。
6. workspace root、媒体目录、缓存目录、trace 目录。
7. memory、web、schedule、image、vision 等 domain service 配置。
8. observer、billing、yuutrace sink 配置。

Kernel 环境由 `PythonKernelConfig` 构造：

```python
ya.PythonKernelConfig(
    python=config.python.executable,
    cwd=workspace.workdir,
    inherit_envs=True,
    env_allowlist=config.python.env_allowlist,
    extra_envs={
        "YUUBOT_RECORDER_URL": config.recorder.local_url,
        "YUUBOT_DAEMON_URL": config.daemon.local_url,
        "YUUBOT_AGENT_TOKEN": issue_kernel_token(conversation),
    },
    sys_path=(
        config.project_src,
        config.plugins_src,
    ),
)
```

部署约定：

1. recorder、daemon、kernel 位于同一可信部署环境。
2. kernel 通过 loopback local API 访问 recorder / daemon。
3. local session token 与 session state 绑定，服务端按 token 解析 Master global scope 或 Group current scope。
4. Group workspace、media cache、trace namespace 按群隔离。
5. Docker 部署将 workspace、media、cache、trace 作为显式 volume。
6. `uv run ybot launch` 管 recorder / NapCat，`uv run ybot up` 管 daemon / 两个 Engine。

## 14. Prompt 结构

Bot prompt 由稳定片段组成：

1. persona 与行为准则。
2. QQ 场景说明、消息格式、回复风格。
3. `execute_python` 使用说明。
4. `import yb` 入口说明。
5. `SESSION_STATE` 关键字段说明。
6. `TASKS` 与长任务约定。
7. 当前 Character 的 `import_modules_for(bot_kind)` 中被 `expand_functions_for(bot_kind)` 命中的函数文档，由 `yuuagents` 注入到 `execute_python` tool description。
8. Master prompt 的安全条目只保留发言审查、隐私判断和高影响操作确认，并可展示当前 Character 的 Master delegate targets。
9. Group prompt 的安全条目说明 RestrictedPython 执行器限制；不要用 prompt 承担联系人/跨群等能力过滤，也不要渲染 delegate 目标。

Prompt 中的能力说明与代码保持同源：

1. 模块 docstring 提供 facade 总览。
2. 函数签名提供参数形状。
3. 函数 docstring 第一行提供快速说明。
4. 复杂函数在 docstring 中给出最小示例。
5. `expand_functions` 使用 glob 控制哪些函数可见；`+` 前缀展开完整 docstring，`-` 前缀排除。
6. prompt 只描述通用使用原则，具体 API 文档来自 import metadata。
7. Master/Group 能力差异来自不同 session backend、service scope 和 Character facade，不在同一 prompt 中做上下文条件分支。

## 15. Observability

观测链路覆盖 daemon、agent loop、tool、Python、业务函数和 QQ IO。

事件：

1. `agent.created` / `agent.closed` / `agent.error`
2. `llm.started` / `llm.finished`
3. `tool.started` / `tool.finished`
4. `python.session_started` / `python.cell_started` / `python.cell_finished` / `python.timeout` / `python.session_closed`
5. `agent_fn.started` / `agent_fn.finished`
6. `recorder.message_received` / `recorder.message_sent`
7. `delegate.started` / `delegate.finished`

日志字段：

1. `bot_kind`
2. `ctx_id`
3. `group_id`
4. `conversation_id`
5. `agent_id`
6. `character_name`
7. `task_id`
8. `user_id`
9. `message_id`
10. `tool_name`
11. `function_name`
12. `duration_s`

排查入口：

1. `scripts/conv.py <conversation_id>` 查看 LLM、tool、Python 和回复历史。
2. `scripts/conv.py --ctx <ctx_id>` 按 QQ 上下文收敛。
3. daemon log 按 `agent_id` / `task_id` 追 agent runtime。
4. recorder log 按 `message_id` / `ctx_id` 追 OneBot IO。

## 16. 验证目标

验证以用户可观察行为和稳定边界为主。

`yuuagents` 层：

1. `Agent.steps()` 在 LLM response 后产出 `LlmStep`。
2. tool call 完成后产出 `ToolStep`，结果进入 history。
3. 每个 live agent 的 Python 变量隔离且持久。
4. `SESSION_STATE` 可在 kernel 和 extension package 中读取。
5. `PythonImport` alias 可导入，函数 docstring 进入 tool description。
6. `AgentDefinition.import_modules` 可按 agent 隔离 Python 可导入能力。
7. `expand_functions` 可按 glob 选择、完整展开或排除函数文档。
8. `master_engine` 使用 FullPythonSession，`group_engine` 使用 RestrictedPythonSession。
9. `Engine.close()` 关闭 live agents 与 kernel。

`yuubot` runtime 层：

1. Master 私聊只进入 Master Bot 和 `master_engine`。
2. 群聊只进入 Group Bot 和 `group_engine`，不同群的 conversation、workspace、session state 完全隔离。
3. `/ybot on` 后 Master 私聊可以直接对话；Group 群聊仍必须由 `/yllm` 或 at 触发。
4. 最终 assistant 文本发送到 QQ 并落库。
5. `execute_python` 调用 `yb.send_message(...)` 可发送并落库。
6. inactivity timeout 按 step 间无进展时间计算。
7. 用户新消息在 step 边界进入 live agent history。
8. context 接近上限时，daemon 注入总结请求，让当前 agent 生成总结，再原地替换 `agent.history`，不关闭 live agent。
9. restore 后 conversation history 可继续驱动下一轮。

`agent_fns` 层：

1. Group facade 的 `yb.recent_messages(limit=5)` 只能读取当前群 `ctx_id`。
2. Master facade 的消息搜索、联系人列表、群列表可以覆盖全局视图。
3. `yb.recall_memory(...)` 尊重 Master global scope 或 Group current scope。
4. `yb.save_memory(...)` 写入审计事件。
5. `yb.web_search(...)` 执行 service 限流。
6. `yb.describe_image(...)` 命中缓存时返回相同描述与缓存标记。
7. `yb.delegate(...)` 只在 Master facade 中可用，并记录 parent / child trace；child Python session 在生命周期结束后回收。

`CLI / service` 层：

1. `ybot mem ...`、`ybot web ...`、`ybot im ...` 调用同一 domain service。
2. command handler 与 agent function 对同一操作产生一致 Master/Group scope 行为。
3. recorder 出站限流、mute、安全审查对 CLI 和 agent function 一致生效。

推荐测试入口：

```bash
cd yuuagents && uv run pytest tests/test_rfc2_runtime.py
cd yuubot && uv run pytest tests/test_agent_fns.py
cd yuubot && uv run pytest tests/flows/test_command_behaviors.py
cd yuubot && uv run pytest tests/flows/test_soft_timeout.py tests/flows/test_agent_timeout_semantics.py
cd yuubot && uv run ty check
```
