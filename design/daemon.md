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

**Character**（角色）= 人格 + 业务意图。例如"夕雨"。
**AgentSpec**（能力规格）= tools + sections + constraints。一个 Character 持有多个 AgentSpec，运行时根据 provider/model 能力选择最优的一个。
**PromptSpec** = 运行时产物，可 inspect。

### 数据流

```
Character × RuntimeInfo → PromptSpec → SimplePromptBuilder → yuuagents
```

1. `characters.py` 定义所有角色（人格、tools、sections、skills）
2. `yuuagents.config.yaml` 只提供 provider + model
3. `prompt.py` 的 `build_prompt_spec()` 确定性地派生出完整 prompt 结构
4. `build_system_prompt()` 将 PromptSpec 转为 yuuagents 的 SimplePromptBuilder

### System Prompt 结构

固定顺序：`persona → [sections...] → skills_summary → expanded_skills`

每个 agent 的具体 sections：

| Agent | Sections |
|-------|----------|
| main | persona → skills_summary → expanded:im |
| general | persona → subagents → docker → sleep_mechanism → bootstrap → skills_summary → expanded:im |
| researcher | persona → skills_summary(web) |
| coder | persona → docker → sleep_mechanism |
| mem_curator | persona → skills_summary(mem) → expanded:mem |

### 文件分工

| 文件 | 职责 |
|------|------|
| `prompt.py` | 数据结构（FileRef, Section, AgentSpec, Character, PromptSpec）+ build 函数 |
| `characters.py` | 所有角色定义，CHARACTER_REGISTRY |
| `prompts/yuu.md` | 夕雨 persona（FileRef 热更新） |
| `yuuagents.config.yaml` | 仅 provider + model |

### 热更新边界

| 可热更（每次新 session 重新加载） | 需重启 |
|---|---|
| persona 文件（FileRef） | 端口、进程级配置 |
| provider / model（config reload） | Docker 镜像 |

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

**YAML 兼容**：如果 YAML config 中 agent 有 `tools` 字段（测试覆盖），则优先使用 YAML 定义；否则使用 CHARACTER_REGISTRY。

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

所有行为定义在 `src/yuubot/characters.py`。
