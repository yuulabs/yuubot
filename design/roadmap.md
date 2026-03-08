# 实现路线图

## 总体原则

- 每个阶段产出可运行、可测试的代码
- 优先级：核心骨架 → web → im → mem
- 每个阶段完成后可以独立使用

---

## Phase 0：项目骨架

**目标**：搭建项目基础设施，可以 `pip install -e .` 并运行 `ybot --help`

**任务**：
- [ ] 完善 `pyproject.toml`（依赖、CLI 入口、构建系统）
- [ ] 创建 `src/yuubot/` 包结构（所有 `__init__.py`）
- [ ] 实现 `cli.py`（Click 主入口，注册子命令组占位）
- [ ] 实现 `config.py`（YAML 加载、环境变量替换、路径展开）
- [ ] 实现 `core/models.py`（消息段模型、事件模型）
- [ ] 实现 `core/db.py`（SQLite 连接管理、schema 初始化）
- [ ] 实现 `core/context.py`（ctx_id 映射管理）
- [ ] 实现 `core/onebot.py`（OneBot V11 消息解析/构造）
- [ ] 创建 `config.example.yaml`
- [ ] 基础测试

**产出**：`ybot --help` 可运行，数据库可初始化

---

## Phase 1：Recorder

**目标**：Recorder 进程可以接收 NapCat 消息并落盘

**任务**：
- [ ] 实现 `recorder/server.py`（反向 WS 服务器）
- [ ] 实现 `recorder/store.py`（消息解析 + 写入 SQLite）
- [ ] 实现 `recorder/relay.py`（内部 WS 转发）
- [ ] 实现 `recorder/api.py`（HTTP API 代理）
- [ ] CLI 命令 `ybot launch`
- [ ] 启动脚本 `scripts/start_recorder.sh`
- [ ] 测试：模拟 NapCat WS 事件，验证落盘和转发

**产出**：Recorder 可独立运行，消息持续落盘到 SQLite

`ybot launch`会启动recorder & napcat. `ybot shutdown`会停止这两者。

---

## Phase 2：命令系统

**目标**：命令树、权限系统可用

**任务**：
- [ ] 实现 `commands/tree.py`（树形命令匹配）
- [ ] 实现 `commands/roles.py`（Role 权限系统）
- [ ] 实现 `commands/entry.py`（入口映射）
- [ ] 实现 `commands/builtin.py`（/bot, /help 基础命令）
- [ ] 测试：命令匹配、权限检查

**产出**：命令系统可独立测试，输入文本 → 匹配结果

---

## Phase 3：Daemon 基础

**目标**：Daemon 可以接收消息、解析命令、触发简单响应

**任务**：
- [ ] 实现 `daemon/app.py`（FastAPI 应用 + 生命周期）
- [ ] 实现 `daemon/ws_client.py`（连接 Recorder 内部 WS）
- [ ] 实现 `daemon/dispatcher.py`（消息分发 + 命令解析）
- [ ] 实现 `daemon/agent_runner.py`（yuuagents SDK 集成，先用简单 echo 测试）
- [ ] CLI 命令 `ybot up`
- [ ] 端到端测试：NapCat → Recorder → Daemon → 命令匹配

**产出**：完整消息链路跑通，命令可触发（暂无 skills）

`ybot up`启动bot主程序。不干扰recorder & napcat，以免掉线。`ybot down`也只关闭bot本身而不关闭另外两者，避免掉线。

---

## Phase 4：Web Skill

**目标**：`ybot web` 三个子命令可用

**任务**：
- [ ] 实现 `skills/web/search.py`（Tavily API 搜索）
- [ ] 实现 `skills/web/reader.py`（基于 agent_read.py 重构）
- [ ] 实现 `skills/web/downloader.py`（文件下载）
- [ ] 实现 `skills/web/cli.py`（Click 子命令）
- [ ] 生成 web SKILL.md
- [ ] 测试

**产出**：`ybot web search/read/download` 可独立使用

ybot web此外还需支持login参数（但不在skill中出现）。这是为了人类登录所使用。人类在一开始登录所需的网站，随后一直复用cookies等状态信息直至过期。

---

## Phase 5：IM Skill

**目标**：`ybot im` 三个子命令可用

**任务**：
- [ ] 实现 `skills/im/query.py`（SQLite 消息查询 + FTS）
- [ ] 实现 `skills/im/cli.py`（Click 子命令）
- [ ] 生成 im SKILL.md
- [ ] 测试

**产出**：`ybot im send/search/list` 可独立使用

ybot im还需支持login参数。`ybot im login <im_name>`. 每个im的登录方式不一样，就qq而言，是将napcat的登录端口打印出来通知用户去扫码。

---

## Phase 6：Memory Skill

**目标**：`ybot mem` 四个子命令 + 自动遗忘可用

**任务**：
- [ ] 实现 `skills/mem/store.py`（记忆 CRUD）
- [ ] 实现 `skills/mem/forget.py`（自动遗忘清理）
- [ ] 实现 `skills/mem/cli.py`（Click 子命令）
- [ ] 生成 mem SKILL.md
- [ ] 测试

**产出**：`ybot mem save/recall/delete/show` 可独立使用

---

## Phase 7：Agent 集成

**目标**：Agent 可以使用所有 skills 完成端到端任务

**任务**：
- [ ] 完善 `daemon/agent_runner.py`（注入 skills 文档到 prompt）
- [ ] 实现 `ybot skills install`（安装 SKILL.md 到 yuuagents）
- [ ] 实现 `daemon/scheduler.py`（Cron 定时任务）
- [ ] 端到端测试：用户发消息 → Agent 使用 skills 回复
- [ ] 主动模式测试

**产出**：完整的 yuubot 系统可用

---

## Phase 8：完善与加固

**目标**：生产可用

**任务**：
- [ ] 错误处理与日志完善
- [ ] 自动重连机制
- [ ] 内置命令完善（/bot grand, /bot on/off 等）
- [ ] README 文档
- [ ] 更多测试覆盖

---

## 依赖关系

```
Phase 0 (骨架)
  ├── Phase 1 (Recorder)
  │     └── Phase 3 (Daemon 基础) ← Phase 2 (命令系统)
  │           ├── Phase 4 (Web Skill)
  │           ├── Phase 5 (IM Skill)
  │           └── Phase 6 (Memory Skill)
  │                 └── Phase 7 (Agent 集成)
  │                       └── Phase 8 (完善)
  └── Phase 4 可独立开发（CLI 工具不依赖 Daemon）
```

## 预估依赖列表

```toml
dependencies = [
    "click",              # CLI 框架
    "pyyaml",             # 配置文件
    "aiosqlite",          # 异步 SQLite
    "websockets",         # WebSocket 客户端/服务器
    "fastapi",            # Daemon HTTP 服务
    "uvicorn",            # ASGI 服务器
    "httpx",              # HTTP 客户端（调用 API）
    "yuuagents",          # Agent SDK
    "apscheduler",        # 定时任务
    "python-dotenv",      # .env 文件加载
    "playwright",         # 网页抓取
    "trafilatura",        # 正文提取
]
```

read apis/ to understand the required repos