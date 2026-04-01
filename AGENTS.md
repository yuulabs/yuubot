# yuubot Working Notes

这份文档面向未来在本仓库工作的 Codex / 工程师。目标是提供高信号的项目概览、常用运维入口和快速 triage 线索，而不是完整实现手册。

## 项目概览

`yuubot` 是一个基于 `yuuagents` 的 QQ 机器人，运行时分成两段：

1. `Recorder` 侧负责接收 NapCat 的 OneBot 事件、落库、下载媒体、转发给 daemon。
2. `Daemon` 侧负责路由消息、维护会话、调用 agent / capability，并经由 recorder API 发消息回 QQ。

主流程可以按下面理解：

`NapCat -> recorder.server -> recorder.relay -> daemon.ws_client / dispatcher -> command tree or AgentRunner -> capability/tools -> recorder.api -> NapCat`

这套拆分很重要，因为多数问题都能先判断属于“入站/存储/转发”还是“路由/agent/出站”。

## 关键概念

- `ctx_id`
  统一的上下文编号，群聊和私聊都映射到 `ctx_id`。绝大多数会话、消息检索、记忆、调度都围绕它展开。先确认 `ctx_id`，再看日志和 trace，通常能少走很多弯路。

- `Conversation`
  daemon 侧维护的多轮会话状态，不等于 QQ 消息线程。核心逻辑在 `src/yuubot/daemon/conversation.py`，处理 TTL、auto mode、token rollover、当前 agent 等问题。

- `Character`
  agent 的人格、工具集、可委派子 agent、提示词片段定义。注册入口在 `src/yuubot/characters/__init__.py`，具体角色见 `src/yuubot/characters/*.py`。

- `Capability`
  bot 内建能力，给 agent 暴露成“像 CLI 一样”的工具接口，例如 `im`、`mem`、`web`、`schedule`。框架入口在 `src/yuubot/capabilities/__init__.py`。

- `Recorder` 与 `Daemon`
  `Recorder` 偏 IO 和边界适配，`Daemon` 偏业务决策。排障时先判断问题发生在消息进入系统之前、进入之后但未命中路由、还是 agent 已运行但工具/发信异常。

## 最重要的文件

### 入口与配置

- `src/yuubot/cli.py`
  `ybot` CLI 入口。运维最常用的 `launch / up / down / shutdown`、能力子命令、memory 运维、docker shell 都从这里进。

- `src/yuubot/config.py`
  配置加载的单一入口。会按顺序查找显式 `-c`、环境变量 `YUUBOT_CONFIG`、项目内 `config.yaml`、`~/.yuubot/config.yaml`；还会自动加载同目录 `.env`。`config.yaml` 同时承载 yuubot 配置与生成 `~/.yagents/config.yaml` 所需的 yuuagents 运行配置。配置问题先看这里。

- `pyproject.toml`
  项目脚本、依赖、测试工具配置入口。`ybot = yuubot.cli:cli` 也定义在这里。

### Recorder 侧

- `src/yuubot/recorder/server.py`
  Recorder 主服务。接收 NapCat 反向 WS 事件，写消息、补 `ctx_id`、下载媒体、处理 `/bot on|off` 紧急制动，并向 daemon relay。

- `src/yuubot/recorder/api.py`
  发消息代理层。统一处理出站消息审查、群聊限流、mute、消息落库，以及少量 NapCat HTTP 代理接口。

- `src/yuubot/recorder/store.py`
  入站消息、媒体、forward 的实际存储逻辑。消息落库但行为不对时，通常要看它。

### Daemon 侧

- `src/yuubot/daemon/app.py`
  daemon 进程组装点。把 config、command tree、conversation manager、agent runner、scheduler、ws client 全部连起来。

- `src/yuubot/daemon/dispatcher.py`
  真正的入口调度器。负责 event -> route -> permission -> per-ctx queue -> executor/agent。消息“收到了但没处理”时首先检查这里。

- `src/yuubot/daemon/routing.py`
  纯路由逻辑。负责判断一条消息该落到命令树、`llm continue` 还是忽略。命令触发问题优先看这里和 `tests/test_routing.py`。

- `src/yuubot/daemon/conversation.py`
  会话生命周期、auto mode、TTL、token 限制、当前 agent 管理。会话复用、错乱、意外过期优先看这里。

- `src/yuubot/daemon/agent_runner.py`
  agent 启动、恢复、signal 注入、静默超时、curator 调用的核心。凡是“agent 明明起了但行为不对”通常会走到这里。

- `src/yuubot/daemon/runtime.py`
  agent 运行时装配层，负责 prompt、tool manager、capability context、docker、LLM 初始化。工具暴露不对、docker 不可用、角色运行时环境异常时看这里。

### 协议、模型与命令

- `src/yuubot/core/onebot.py`
  OneBot 消息解析与发送结构转换。段落类型、reply/at/image/forward 的问题经常出在这里。

- `src/yuubot/core/models.py`
  统一的数据模型定义，包含消息 segment、事件结构和 Tortoise ORM 模型。想确认数据库表意和消息序列化方式时看这里。

- `src/yuubot/commands/builtin.py`
  内建命令树定义，包含 `/bot`、`/llm`、`/close`、`/cost`、`/ping`、`/char`。命令行为异常先看这里。

### Agent 与 Capability

- `src/yuubot/characters/__init__.py`
  Character 注册表。新增角色或排查某个角色为何不可用时从这里开始。

- `src/yuubot/prompt.py`
  prompt 结构与 `Character`/`AgentSpec` 定义所在。角色的系统提示拼装问题通常会回到这里。

- `src/yuubot/capabilities/__init__.py`
  capability 注册、命令解析、执行上下文入口。agent 看到的是工具，真正落地到 capability 调度就是这里。

- `src/yuubot/capabilities/*/contract.yaml`
  各 capability 的面向 agent 的“文档与约束”。想知道某个 capability 该怎么被调用，优先看 contract，不要直接猜 CLI 形状。

### 运维与测试辅助

- `scripts/conv.py`
  查看 `yuuagents` trace 的首选工具。排查 agent 调用了什么工具、何时停住、实际回复了什么，比单看日志更快。

- `tests/flows/`
  跨模块行为回归测试，尤其适合验证会话、权限、group discovery、soft timeout 等系统行为。

- `tests/test_routing.py`
  路由逻辑的最小真相源。

- `tests/test_agent_runner.py`
  agent session 生命周期的最小回归入口。

## 常用运维工具

### `ybot` CLI

先看总帮助：

```bash
uv run ybot --help
```

最常用命令：

```bash
uv run ybot launch
uv run ybot up
uv run ybot down
uv run ybot shutdown
uv run ybot shutdown --recorder-only
```

含义：

- `launch`: 启动 NapCat 和 recorder，通常在 `screen` 后台会话里运行 recorder。
- `up`: 前台启动 daemon。
- `down`: 通过 daemon API 请求优雅关闭。
- `shutdown`: 关闭 recorder，默认也会关闭 NapCat。

常见辅助命令：

```bash
uv run ybot mem list
uv run ybot mem list --trash
uv run ybot mem restore 12 13
uv run ybot web login
uv run ybot im login
uv run ybot docker shell
```

说明：

- `mem list` / `mem restore` 适合排查记忆是否真的落库、是否被移入垃圾桶。
- `web login` 用于人工登录网页能力所需账号并持久化 cookie。
- `im login` 用于查看 NapCat WebUI 登录入口。
- `docker shell` 用于进入 yuuagents 容器看运行环境，仅在 agent 依赖 docker 工具时有意义。

提醒：

- `ybot` 的不少 capability 子命令是从 contract 动态注册出来的，先跑 `uv run ybot <cap> --help` 再操作，不要假设参数名。
- `launch` / `shutdown` 涉及 recorder 与 NapCat；`up` / `down` 只管 daemon。

### `scripts/conv.py`

这是排查 agent 行为最有价值的工具之一：

```bash
uv run python scripts/conv.py
uv run python scripts/conv.py -l
uv run python scripts/conv.py <conv_id_or_prefix>
uv run python scripts/conv.py <conv_id> -n
uv run python scripts/conv.py <conv_id> --tool im
uv run python scripts/conv.py --agent main --limit 10
uv run python scripts/conv.py <conv_id> --grep "关键词"
```

适用场景：

- 想确认 agent 到底有没有调用 capability/tool。
- 想知道工具入参是不是模型自己拼坏了。
- 想区分是模型没做事，还是工具返回了异常结果。
- 想看同一个 conversation 在 resume 前后发生了什么。

按上下文排查时优先用 `--ctx <ctx_id>`，可以把输出快速收敛到同一个群聊/私聊对应的会话。

## 快速 triage 手册

- 收不到消息或 `ctx_id` 异常
  先看 `screen -r napcat`、`screen -r recorder`，再看 `src/yuubot/recorder/server.py`、`src/yuubot/core/onebot.py`、`src/yuubot/recorder/store.py`。

- 消息已进系统，但没有触发任何命令 / LLM
  先查 `src/yuubot/daemon/routing.py` 和 `src/yuubot/daemon/dispatcher.py`，再对照 `tests/test_routing.py`。

- 会话串台、过期、auto mode 不对
  先看 `src/yuubot/daemon/conversation.py`，再看相关 flow tests。

- agent 启动了，但行为、工具、delegate、静默超时不对
  先查 `src/yuubot/daemon/agent_runner.py`、`src/yuubot/daemon/runtime.py`、`src/yuubot/characters/*.py`，同时用 `scripts/conv.py` 看 trace。

- capability 调用报错，或 agent 看不到某个 capability
  先看 `src/yuubot/capabilities/__init__.py`、对应 capability 模块、对应 `contract.yaml`，确认 action 名和 payload 形状。

- bot 发不出去消息，或被限流/拦截
  先看 `src/yuubot/recorder/api.py`。这里统一处理 mute、安全审查、群聊限流、bot 消息落库。

## 日志与运行时定位

默认日志目录是 `~/.yuubot/logs`，关键文件：

- `~/.yuubot/logs/daemon.log`
- `~/.yuubot/logs/recorder.log`

常用检索：

```bash
grep "ctx=123" ~/.yuubot/logs/daemon.log
grep "task_id=abc123" ~/.yuubot/logs/daemon.log
grep "ctx 123 muted" ~/.yuubot/logs/recorder.log
```

默认端口也值得记住：

- NapCat WS: `8765`
- Recorder relay WS: `8766`
- Recorder API: `8767`
- Daemon API: `8780`
- NapCat HTTP: `3000`

端口或 API 问题先核对配置是否被 `config.yaml` / `.env` 覆盖。

## 开发与验证

常用命令：

```bash
uv sync
uv run pytest
uv run pytest tests/test_routing.py
uv run pytest tests/test_agent_runner.py
uv run pytest tests/flows/
uv run ruff check src tests
uv run ruff format src tests
uv run ty check
```

约定：

- 保持 `from __future__ import annotations`、类型标注、4 空格缩进。
- 序列化/配置模型优先沿用 `msgspec.Struct`，运行时可变状态优先沿用 `attrs`。
- 新增行为优先补 focused regression test，不要只靠手工跑 bot。

### 测试准则

- 默认优先写 E2E / flow 测试。首选入口是实际用户会走到的边界：`Dispatcher`、`ybot` CLI、脚本入口、Recorder API。
- 测试应断言用户可观察到的结果：是否回消息、会话是否建立/关闭、权限是否生效、trace / DB / CLI 输出是否符合预期。
- 可以让 E2E 触发内部细节，但不要直接测试内部细节本身。比如不要为了覆盖而直接断言私有 helper、内部中间结构、builder 拼装细节、route 对象细节、prompt section 拼接细节。
- 允许 mock 外部依赖和系统边界，例如 LLM、HTTP API、NapCat、第三方网站；尽量不要 mock 本仓库内部模块之间的交互。
- 如果一个特性已经能通过更高层入口稳定覆盖，就删掉对应的底层单元测试，避免一处重构导致大量“实现绑定型”测试一起碎掉。
- 改路由、会话、capability、sandbox、flow 相关逻辑时，优先补到 `tests/flows/` 或 CLI / script 级行为测试；只有当某个约束无法从边界稳定观测时，才补最小必要的低层测试。
- 写测试前先问自己：这个断言描述的是“用户得到什么”，还是“代码现在怎么实现”。如果是后者，通常应该换成更高层的行为断言。

## 提交与变更说明

- 提交信息沿用 `feat:`、`fix:`、`refactor:`、`chore:` 这类短 imperative 前缀。
- PR 描述应说明用户可见影响、验证命令，以及是否涉及 config / API / 跨包行为。
- 如果修改的是运行链路，最好在说明里明确属于 recorder 侧、daemon 侧，还是两边都改了。
