# 能力模型：Addons + Skills

## 两层模型

### Addons（内置能力）

yuubot 自身提供的能力。**看起来是 CLI，实际是函数调用**。

通过 `execute_addon_cli` 工具统一调用：

```
execute_addon_cli("im send --ctx 5 -- [{\"type\":\"text\",\"text\":\"hello\"}]")
```

- `--` 左侧：CLI 风格参数（shlex 解析）
- `--` 右侧：可选的结构化 JSON 数据（直接 json.loads，无需转义）
- 返回：**多模态**（文本 + 图片等），不是纯 stdout

两档加载：
- **Expanded addons**（高频，如 im）：完整文档常驻 system prompt
- **On-demand addons**（中频，如 mem）：最小描述在 prompt，用 `read_addon_doc("mem")` 按需加载

包含：im, web, mem, schedule, img, hhsh。

### Skills（第三方/通用能力）

Anthropic 标准 Agent Skills。从外部安装，像第三方包。
- `./skills/` 目录，SKILL.md + scripts
- Agent 通过 bash 执行 skill 脚本
- 保留 `execute_skill_cli` + `read_skill` 工具用于第三方 skills

## 实现

### 目录结构

```
src/yuubot/addons/
├── __init__.py      # 注册表 + execute() 路由 + 命令解析
├── tools.py         # execute_addon_cli + read_addon_doc (yuutools Tool)
├── docs/            # 每个 addon 的文档 (替代 SKILL.md)
│   ├── im.md
│   ├── mem.md
│   ├── web.md
│   ├── img.md
│   ├── schedule.md
│   └── hhsh.md
├── im.py            # send, search, browse, list
├── web.py           # search, read, download
├── mem.py           # save, recall, delete, show, config
├── img.py           # save, search, delete, list
├── schedule.py      # create, list, update, delete
└── hhsh.py          # guess
```

### 核心组件

**AddonContext**：每次 addon 调用的运行时上下文
- `config`: yuubot Config 实例
- `ctx_id`: 当前会话 ctx
- `user_id`, `user_role`: 调用者信息
- `agent_name`, `task_id`: agent 运行信息

**命令解析**：`_parse_command("im send --ctx 5 -- [...]")`
1. 用 `" -- "` 分割 CLI 部分和 JSON data
2. shlex 解析 CLI 部分得到 tokens
3. 第一个 token = addon 名，第二个 = 子命令，其余 = 参数
4. `--` 后的部分直接 `json.loads()`

**路由**：addon 名 → 注册表查找实例 → 子命令 → 方法调用

### AgentSpec 字段

```python
AgentSpec(
    tools=["execute_addon_cli", "read_addon_doc", ...],
    addons=["*"],           # 可用 addon 列表
    expand_addons=["im"],   # 文档常驻 prompt 的 addon
    skills=["*"],           # 第三方 skills（保留）
    expand_skills=[...],    # 展开到 prompt 的 skills
)
```

### 工具注册

addon tools 定义在 `yuubot.addons.tools`，使用 `@yt.tool()` 装饰器。
在 `AgentRunner._build_tool_manager()` 中与 yuuagents builtin tools 一起注册到 ToolManager。
通过 `AgentContext.addon_context` 字段传递 AddonContext 到工具函数。

## 与旧 Skill 系统的关系

- `skills/` 目录下的实现（query.py, store.py, formatter.py 等）作为**底层库**保留
- Addon 调用这些底层库，不再经过 Click CLI 包装
- `ybot` CLI 命令保留供人工使用，内部仍调用 `skills/*/cli.py`
- 第三方 skills 仍通过 `execute_skill_cli` + subprocess 执行
