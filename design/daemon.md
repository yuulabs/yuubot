# Daemon 进程设计

## 职责

Daemon 是 yuubot 的核心进程，负责：
1. **接收事件** — 连接 Recorder 内部 WS，接收转发的消息事件
2. **命令解析** — 树形命令匹配 + 权限检查
3. **Agent 驱动** — 使用 yuuagents SDK 创建并运行 Agent
4. **定时任务** — Cron 触发主动模式

## 启动方式

```bash
ybot up [--config config.yaml]
```

## Prompt 管理

### 核心概念

**Character**（角色）= 人格 + 能力规格。例如"夕雨"。每个 Character 持有一个 AgentSpec。
**AgentSpec**（能力规格）= tools + sections + constraints。
**PromptSpec** = 运行时产物，可 inspect。

### 数据流

```
Character.spec + RuntimeInfo → PromptSpec → SimplePromptBuilder → yuuagents
```

1. `characters/` 包定义所有角色（人格、tools、sections、skills），每个角色一个文件
2. `yuuagents.config.yaml` 仅提供 provider + model（Character.provider/model 可运行时覆盖）
3. `prompt.py` 的 `build_prompt_spec()` 确定性地派生出完整 prompt 结构
4. `build_system_prompt()` 将 PromptSpec 转为 yuuagents 的 SimplePromptBuilder

### System Prompt 结构

固定顺序：`persona → [sections...] → skills_summary → expanded_skills`

每个 agent 的具体 sections：

| Agent | Sections |
|-------|----------|
| main | persona → safety → messaging → memes → skills_summary → expanded:im |
| general | persona → subagents → docker → sleep_mechanism → bootstrap → skills_summary → expanded:im |
| researcher | persona → skills_summary(web) |
| coder | persona → docker → sleep_mechanism |
| mem_curator | persona → skills_summary(mem) → expanded:mem |

### 文件分工

| 文件 | 职责 |
|------|------|
| `prompt.py` | 数据结构（FileRef, Section, AgentSpec, Character, PromptSpec）+ build 函数 |
| `characters/__init__.py` | CHARACTER_REGISTRY, register/unregister, 共享 sections |
| `characters/main.py` | 夕雨角色定义 |
| `characters/general.py` | 通用助手角色 |
| `characters/researcher.py` | 研究助手角色 |
| `characters/coder.py` | 编码代理角色 |
| `characters/curator.py` | 记忆整理角色 |
| `prompts/main/persona.md` | 夕雨人格 |
| `prompts/main/safety.md` | 安全规则 |
| `prompts/main/messaging.md` | 群聊发消息原则 |
| `prompts/main/memes.md` | 表情包使用 |
| `yuuagents.config.yaml` | 仅 provider + model |

### 热更新边界

| 可热更（每次新 session 重新加载） | 需重启 |
|---|---|
| persona 文件（FileRef 热加载） | 端口、进程级配置 |
| provider / model（`/ychar config` 运行时修改） | Docker 镜像 |

### /ychar 命令

| 命令 | 说明 |
|------|------|
| `/ychar list` | 列出所有已注册 Character |
| `/ychar show prompt [name]` | 显示系统提示词结构与大小 |
| `/ychar show config [name]` | 显示 Character 配置详情 |
| `/ychar config <name> provider=x model=y` | 运行时热更新 provider/model |

## 模块设计

### app.py — FastAPI 应用

FastAPI + lifespan 管理：
- 加载配置，连接 Recorder WS
- 初始化 Dispatcher, Scheduler, AgentRunner
- 提供 `/health`, `/agent/status`, `/agent/trigger` 接口

### dispatcher.py — 消息分发

职责：解析命令 → 权限检查 → 分发到 executor 或 agent。

关键机制：
- **Per-ctx worker**：每个 ctx 一个 `_CtxWorker`，保证同一上下文内串行处理
- **Session 管理**：活跃 session 内支持续对话，无需重复命令前缀
- **Ping 机制**：如果 agent 正在运行，新消息通过 Ping 注入而非排队
- **Auto mode**：DM 中 `/bot on --auto` 开启自动续对话

### agent_runner.py — Agent 运行器

三个入口：
- `run()` — 被动模式（命令触发）
- `run_scheduled()` — 主动模式（Cron 触发）
- `_run_agent()` — 内部调用（delegation, curator）

所有入口统一调用 `_build_prompt()` 构建 prompt：
```python
prompt_spec, prompt_builder = self._build_prompt(agent_name)
```

`_build_prompt()` 只查 CHARACTER_REGISTRY，不回退到 YAML。YAML 仅用于 provider/model 配置。

### 消息渲染

消息格式化在 `skills/im/formatter.py`：

```xml
<reply to="夕雨yuu">不是哦...</reply>
<msg name="繁星入梦" qq="948523603" time="03-10 11:11">为什么喜欢百合</msg>
```

- `name` 取最佳名称（alias > display_name > nickname）
- 时间格式紧凑（MM-DD HH:MM）
- reply 作为前置同级元素，与 msg 分离

### Docker 运行时上下文

当 Agent 使用 `execute_bash` / 文件类工具时，Daemon 注入 subprocess env：
- `YUU_DOCKER_HOST_MOUNT=/mnt/host`
- `YUU_DOCKER_HOME_DIR`
- `YUU_DOCKER_HOME_HOST_DIR`

### session.py — 会话管理

- Session = user-visible 连续对话单元，绑定 (ctx_id, agent_name)
- 支持 TTL 超时、token limit rollover
- Rollover 时：压缩摘要 → handoff_note → 新 session → 触发 mem_curator

### summarizer.py — 摘要生成

Session rollover 时生成 handoff note，注入下一轮 session。

## 响应规则

| 场景 | 默认行为 |
|------|----------|
| Master 私聊 | free 模式，直接响应 |
| 其他人私聊 | 需要 `/bot allow-dm` 开启 |
| 群聊 @bot | at 模式，响应 |
| 群聊命令（free模式） | 需要 Master 开启 `/bot on --free` |

## 配置

```yaml
# yuuagents.config.yaml — 只保留 provider + model
agents:
  main:
    provider: deepseek
    model: deepseek-chat
  general:
    provider: deepseek
    model: deepseek-chat
```

所有行为定义在 `src/yuubot/characters/` 包中。
