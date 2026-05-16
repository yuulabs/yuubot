# TODO: External Integration Plugin（外部子进程插件）

## 背景

当前 integration 是 in-process 的 Python 类，通过 entry points 或硬编码注册。这意味着：

- 添加新 integration 需要 `pip install` + 重启 daemon。
- 插件的依赖和 daemon 共享同一个 Python 环境，版本冲突风险高。
- 插件 bug 可能 crash 整个 daemon 进程。

对于快速迭代的第三方服务（day-0 适配新 app），需要一种**不停机、依赖隔离、故障隔离**的插件安装方式。

## 定位

外部插件是 **day-0 快速集成** 的补充通道，不替代核心 integration：

| 场景 | 推荐方式 |
|------|----------|
| QQ（NapCat）、核心通信渠道 | in-process integration，随 daemon 发布 |
| Telegram、Discord 等稳定渠道 | in-process integration，pip install |
| 新出的 app / 实验性集成 / 社区贡献 | **外部插件** |

## 设计

### 架构：子进程 + 独立 venv + HTTP 协议

```
daemon (主进程)
├── plugin-manager (生命周期管理)
│   ├── lotion-plugin (子进程, 独立 venv, port 9001)
│   ├── bluesky-plugin (子进程, 独立 venv, port 9002)
│   └── ...
└── reverse proxy: /integration/{plugin_name} → localhost:{port}
```

每个外部插件是一个独立 Python 进程，拥有自己的 virtualenv，通过 HTTP 与 daemon 通信。

### 插件包格式

```
~/.yuubot/plugins/lotion/
├── pyproject.toml          # 插件自己的依赖声明
├── manifest.yaml           # 元数据 + 入口 + facade 声明
└── lotion_plugin/
    ├── __init__.py
    ├── ingress.py          # 接收外部 webhook → POST 到 daemon
    └── facade.py           # 暴露给 agent 的函数 handler
```

### manifest.yaml

```yaml
name: lotion
version: "0.2.1"
description: "Lotion (Notion-like) integration for yuubot"
entry: lotion_plugin
requires_python: ">=3.11"

# 插件接收外部 webhook 的路由
ingress:
  routes:
    - path: /webhook
      method: POST
      description: "Lotion event webhook"

# 暴露给 agent 的 facade 函数
facade:
  namespace: lotion
  functions:
    - name: search
      description: "Search pages by keyword"
      params:
        query: { type: str, description: "Search query" }
      returns: "list[dict]"
    - name: create_page
      description: "Create a new page"
      params:
        title: { type: str, description: "Page title" }
        content: { type: str, description: "Markdown content" }
      returns: "dict"

# 插件需要的系统依赖（daemon 安装前检查）
requires_system: []

# 插件的 config schema（admin UI 自动生成表单）
config:
  type: object
  properties:
    api_key:
      type: string
      format: secret
    workspace_id:
      type: string
```

### Daemon 分配给插件的资源

| 资源 | 传递方式 | 说明 |
|------|----------|------|
| data_dir | `YUUBOT_DATA_DIR` | 插件私有目录，daemon 保证存在，删除时 rmtree |
| ingest endpoint | `YUUBOT_INGEST_URL` | 插件往这里 POST 标准 InboundMessage |
| plugin token | `YUUBOT_PLUGIN_TOKEN` | 调 ingest 时的 Bearer auth |
| listen port | CLI arg `--port` | daemon 分配，插件在此端口监听 |

插件进程启动命令：

```bash
cd ~/.yuubot/plugins/lotion && \
  YUUBOT_DATA_DIR=~/.yuubot/data/integrations/lotion \
  YUUBOT_INGEST_URL=http://127.0.0.1:8780/ingest \
  YUUBOT_PLUGIN_TOKEN=<generated> \
  uv run python -m lotion_plugin --port 9001
```

### 安装流程

1. Admin 上传插件包（zip）或填写 PyPI 包名 / git URL
2. Daemon 解压到 `~/.yuubot/plugins/{name}/`
3. Daemon 读取 `manifest.yaml`，校验格式
4. `uv venv && uv pip install .`（标准 Python build pipeline，不执行自定义脚本）
5. 写入 integration 记录到 DB → 触发 `ResourceChanged`
6. Plugin manager 启动子进程
7. 现有 reconcile → actor reload 链路自动刷新 facade 和 system prompt

### 安装约束

- **不允许自定义安装脚本**。安装路径只走 `uv pip install .`，即标准 Python build backend。
- 需要系统库的插件在 `manifest.requires_system` 声明，daemon 安装前用 `which`/`ldconfig` 检查，缺失则报错，不代劳安装。
- 鼓励插件作者发布 pre-built wheel，把编译留在 CI 而不是用户机器上。
- 如果插件的环境需求超出 Python 包能覆盖的范围，应该走容器化部署（不在本 issue 范围内）。

### 通信协议

#### 插件 → Daemon（投递消息）

```http
POST {YUUBOT_INGEST_URL}
Authorization: Bearer {YUUBOT_PLUGIN_TOKEN}
Content-Type: application/json

{
  "integration_id": "lotion-abc123",
  "message_id": "evt-001",
  "sender_id": "user-42",
  "sender_name": "Alice",
  "kind": "private",
  "text": "New comment on page: ...",
  "segments": [{"kind": "text", "text": "..."}]
}
```

#### Daemon → 插件（facade 调用）

```http
POST http://localhost:{port}/facade/{function_name}
Authorization: Bearer {internal_token}
Content-Type: application/json

{"query": "meeting notes"}
```

#### Daemon → 插件（健康检查）

```http
GET http://localhost:{port}/health
```

返回 200 表示存活。连续 N 次失败触发自动重启。

### 生命周期管理

| 事件 | 行为 |
|------|------|
| daemon 启动 | 扫描已安装插件，按记录启动子进程 |
| 插件安装 | venv 创建 → 启动子进程 → 注册路由 |
| 插件升级 | 停旧进程 → `uv pip install .` → 启新进程（在途请求 drain） |
| 插件卸载 | 停进程 → rmtree(plugin_dir) → rmtree(data_dir) → 删 DB 记录 |
| 插件崩溃 | 自动重启，指数退避，连续失败后标记 unhealthy 通知 admin |
| daemon 停止 | 向所有插件子进程发 SIGTERM，等待 graceful shutdown |

### Facade 自动生成

Daemon 从 `manifest.yaml` 的 `facade` 段读取函数声明，自动生成 `yext` 代理模块。Agent 调用链：

```
yb.lotion.search(query="...")
  → yext proxy → HTTP POST daemon /agent-fns/lotion/search
    → daemon → HTTP POST plugin:port /facade/search
      → 插件执行 → 返回 JSON
```

Actor reload 时（由 `ResourceChanged` 触发），新的 facade 函数自动注入 agent 的 Python session 和 system prompt。

## 影响范围

- `runtime/plugin_manager.py`（新增）— 插件生命周期：安装、启动、停止、重启、卸载
- `runtime/daemon.py` — 集成 plugin manager，启动/停止时协调
- `runtime/admin.py` — 插件安装/卸载/升级 API 端点
- `core/integrations/core.py` — 外部插件的 integration 记录写入、reconcile 适配
- `core/facade.py` — 从 manifest 生成 facade 代理模块
- `bootstrap/config.py` — `paths.plugins_dir` 配置项
- Admin UI — 插件管理页面（安装、状态、日志查看）

## 非目标

- 容器化插件（`type: container`）——留作未来 escape hatch，本轮不实现。
- 插件市场 / 中心化分发——先手动上传或填 URL。
- 插件间通信——插件只和 daemon 通信，不互相调用。
- 多语言插件（非 Python）——协议是 HTTP 所以理论上可以，但工具链只支持 Python。
- 插件沙箱化（限制网络/文件访问）——靠 admin 信任 + 进程隔离，不做 seccomp/namespace。
