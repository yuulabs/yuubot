# yuubot 目录结构与模块职责

## 完整目录结构

```
yuubot/
├── pyproject.toml                  # 项目配置、依赖、CLI入口
├── config.example.yaml             # 配置文件示例
├── README.md
│
├── scripts/
│   ├── start_napcat.sh             # NapCat 启动脚本
│   └── start_recorder.sh           # Recorder + NapCat 联合启动脚本
│
├── design/                         # 设计文档
│   ├── design.md                   # 原始设计文档
│   ├── architecture.md             # 架构总览
│   ├── directory.md                # 本文件
│   ├── recorder.md                 # Recorder 进程设计
│   ├── daemon.md                   # Daemon 进程设计
│   ├── commands.md                 # 命令系统与权限设计
│   ├── skills.md                   # Skills 详细设计
│   ├── config.md                   # 配置文件格式
│   ├── database.md                 # 数据库 schema
│   └── roadmap.md                  # 实现路线图
│
├── agent_read.py                   # 参考实现（web skill 原型）
│
├── src/yuubot/
│   ├── __init__.py
│   ├── cli.py                      # Click CLI 主入口 (ybot)
│   ├── config.py                   # 配置加载与校验
│   │
│   ├── core/                       # 核心公共模块
│   │   ├── __init__.py
│   │   ├── models.py               # 数据模型（消息段、事件、ctx 等）
│   │   ├── db.py                   # SQLite 连接管理
│   │   ├── context.py              # ctx_id 映射管理器
│   │   └── onebot.py               # OneBot V11 协议解析/构造
│   │
│   ├── recorder/                   # Recorder 进程
│   │   ├── __init__.py
│   │   ├── server.py               # 反向 WS 服务器（接收 NapCat）
│   │   ├── store.py                # 消息存储逻辑（写 SQLite）
│   │   ├── relay.py                # 内部 WS 转发（推送给 daemon）
│   │   └── api.py                  # HTTP API（供 daemon/skills 调用 NapCat）
│   │
│   ├── daemon/                     # Daemon 进程
│   │   ├── __init__.py
│   │   ├── app.py                  # FastAPI 应用 + 生命周期管理
│   │   ├── ws_client.py            # 连接 Recorder 内部 WS 的客户端
│   │   ├── dispatcher.py           # 消息分发（命令解析 → Agent 触发）
│   │   ├── scheduler.py            # 定时任务（主动模式 Cron）
│   │   └── agent_runner.py         # Agent 创建与运行（yuuagents SDK）
│   │
│   ├── commands/                   # 命令系统
│   │   ├── __init__.py
│   │   ├── tree.py                 # 命令树（树形匹配）
│   │   ├── roles.py                # Role 权限系统
│   │   ├── builtin.py              # 内置命令（/bot, /help 等）
│   │   └── entry.py                # 入口映射（/y, /yuu 等）
│   │
│   └── skills/                     # CLI Skills（agent 调用的工具）
│       ├── __init__.py
│       ├── im/                     # IM skill
│       │   ├── __init__.py
│       │   ├── cli.py              # ybot im send/search/list
│       │   └── query.py            # 消息查询逻辑
│       │
│       ├── web/                    # Web skill
│       │   ├── __init__.py
│       │   ├── cli.py              # ybot web search/read/download
│       │   ├── search.py           # Tavily 搜索
│       │   ├── reader.py           # 网页阅读（基于 agent_read.py）
│       │   └── downloader.py       # 文件下载
│       │
│       └── mem/                    # Memory skill
│           ├── __init__.py
│           ├── cli.py              # ybot mem save/recall/delete/show
│           ├── store.py            # 记忆存储逻辑
│           └── forget.py           # 自动遗忘系统
│
└── tests/
    ├── conftest.py
    ├── test_models.py
    ├── test_commands.py
    ├── test_recorder.py
    ├── test_daemon.py
    └── test_skills/
        ├── test_im.py
        ├── test_web.py
        └── test_mem.py
```

## 模块职责说明

### `src/yuubot/cli.py`
Click CLI 主入口。注册所有子命令组：
- `ybot up` — 启动 daemon
- `ybot recorder` — recorder 管理
- `ybot im` — IM skill
- `ybot web` — Web skill
- `ybot mem` — Memory skill
- `ybot skills` — Skill 安装管理

### `src/yuubot/config.py`
加载 `config.yaml`，校验必填字段，提供全局配置访问。包括：
- NapCat 连接信息
- Recorder 端口配置
- Agent persona / skills 配置
- 数据库路径
- API keys

### `src/yuubot/core/`
所有进程共享的核心模块：
- **models.py** — 消息段（TextSegment, ImageSegment, AtSegment）、OneBot 事件模型、ctx 模型
- **db.py** — SQLite 连接池/管理器，供 recorder 和 skills 共用
- **context.py** — ctx_id ↔ (group_id/user_id) 的双向映射，启动时从 DB 热加载
- **onebot.py** — OneBot V11 消息解析（CQ码/JSON → 内部模型）和构造（内部模型 → OneBot JSON）

### `src/yuubot/recorder/`
独立进程，与 NapCat 绑定：
- **server.py** — 启动反向 WS 服务器，NapCat 连接过来推送事件
- **store.py** — 将消息事件解析后写入 SQLite
- **relay.py** — 内部 WS 服务器，将事件转发给 daemon
- **api.py** — HTTP API，代理 NapCat 的 HTTP 接口（发送消息等），供 daemon 和 skills 调用

### `src/yuubot/daemon/`
Bot 主进程：
- **app.py** — FastAPI 应用，管理生命周期（启动时连接 recorder、初始化 agent、启动 scheduler）
- **ws_client.py** — 连接 recorder 内部 WS，接收转发的事件
- **dispatcher.py** — 事件分发：命令解析 → 权限检查 → 触发 agent 或内置命令
- **scheduler.py** — APScheduler 集成，Cron 定时触发主动模式
- **agent_runner.py** — 使用 yuuagents SDK 创建 Agent，注册 tools，运行任务

### `src/yuubot/commands/`
命令系统（借鉴旧 qqbot 设计，全新实现）：
- **tree.py** — 树形命令匹配（RootCommand → Command → 叶子节点 executor）
- **roles.py** — Role 权限系统（Master > Mod > Folk > Deny）
- **builtin.py** — 内置管理命令（/bot on/off, /bot grand 等）
- **entry.py** — 入口映射（/y, /yuu → 去掉前缀后匹配命令树）

### `src/yuubot/skills/`
CLI 工具，agent 通过 `ybot <skill> <command>` 调用：
- **im/** — 消息收发、搜索、列表
- **web/** — 网页搜索（Tavily）、阅读（Playwright）、下载
- **mem/** — 记忆存储、检索、删除、自动遗忘
