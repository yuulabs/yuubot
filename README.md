# yuubot

yuubot 是一个面向 Agent 用户的本地控制台：你可以在 Web UI 里配置 OpenAI-compatible Gateway、集成和
Actor，然后让 Agent 在隔离工作区中执行代码、调用工具，并通过 WebSocket 进行对话。

![yuubot Admin Actors 页面](docs/images/admin-actors.png)

## 项目结构

```text
src/yuubot/   后端运行时
src/yb/       任务与 office 辅助模块
src/yext/     扩展集成
tests/        后端测试
web/          React Admin UI
design/       架构设计文档
```

## 快速开始

### 1. 准备环境

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- [pnpm](https://pnpm.io/)（构建前端时需要）

### 2. 安装依赖

```bash
git clone git@github.com:yuulabs/yuulabs/yuubot.git
cd yuubot
uv sync
```

### 3. 创建 yuubot 配置

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 只保存进程启动配置。Gateway、Integrations、Actors 和 Routes 在 Admin UI 中
配置，并持久化到 `data_dir` 下的数据库。数据默认写入 `.yuubot-data/`。

### 4. 构建前端并启动

```bash
cd web && pnpm install && pnpm run build && cd ..
uv run ybot serve config.yaml
```

打开 <http://127.0.0.1:8765>。

### 5. 配置 Gateway

打开 Admin UI 的 **Gateway** 页面，添加一个或多个标准 OpenAI-compatible Endpoint。
Endpoint API key 可为空，本地服务也能直接接入；非空 key 会加密保存且不会由 API 返回。
随后创建 Alias，手工声明其接受的输入模态，并按优先级配置 `endpoint/model` targets。
Actor 可以选择 Alias，也可以精确选择一个 `endpoint/model`。

yuubot 记录 tokens、延迟、实际目标和 fallback 路径，但不计算金额。OpenAI-compatible
协议没有统一账单字段，缓存写入、搜索和多模态的计费也持续变化。需要准确成本、预算、
限流或供应商治理时，请部署自己的 OpenAI-compatible gateway，再把它作为普通 Endpoint 接入。

开发时可用 Vite dev server：

```bash
uv run ybot serve config.yaml &
cd web && pnpm run dev
```

Vite 会把 `/api` 代理到 `127.0.0.1:8765`。

## 常用命令

```bash
uv run ybot check config.yaml
uv run ybot migrate config.yaml --dry-run --json
uv run ybot status config.yaml --json
uv run ybot chat config.yaml amy "hello"
uv run pytest -q
```

## 本地数据

`data_dir` 下的常见路径：

```text
<data_dir>/db/yuubot.db
<data_dir>/workspace/<actor_id>/
<data_dir>/logs/
<data_dir>/kv/
<data_dir>/published/
```

## 服务器部署

公网单机部署使用 `scripts/deploy-server.sh`，详见
[docs/server-deploy.md](docs/server-deploy.md)。

## 开发者

```bash
uv run ruff check src tests
uv run ty check
uv run pytest -q
cd web && pnpm run check && pnpm run build
```

系统核心流程、扩展点与全部外部 facade 见
[`design/system-design.md`](design/system-design.md)。`AGENTS.md` 保存开发约束，其他
`design/` 文档是专题补充；`design/archive/` 只用于历史追溯。
