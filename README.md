# Yuubot

> 一个面向真正工作流的自托管 AI Agent 工作台：Skills 按需加载，Python 负责工具编排，
> Workspace 直接连接浏览器，执行过程从头到尾流式可见。

Yuubot 不只是给模型套一层聊天界面。它让一个或多个 Actor 拥有长期身份、独立工作区和持久
Conversation；你在浏览器里提出任务，Actor 可以研究资料、处理文件、运行代码、调用外部服务，
并把报告、图表或网页直接留在 Workspace 中。聊天、文件、HTML 预览、分享和运行审计都在同一个
Web UI 里完成。

![Yuubot Admin Actors 页面](docs/images/admin-actors.png)

## 为什么是 Yuubot

### 1. Skills 按需加载，而不是塞满上下文

很多 Agent 框架会把所有能力说明一次性放进 system prompt。Skill 越装越多，上下文越臃肿，
模型反而更难找到真正相关的指令。

Yuubot 把 Skill 分成“发现”和“加载”两个阶段：

- 全局 Skill catalog 不会整包注入 Actor prompt；
- Actor 只看到当前 `loaded` Skills 的名称和简短摘要；
- 完整 `SKILL.md` 只有在任务需要时才由模型读取；
- 不需要的 Skill 可以在 Actor 页面禁用，也可以直接在对话里让 Actor 将其标记为
  `loaded: false`。后续 prompt 不再出现它，无需修改启动配置或重启 Yuubot。

这让 Skill 保持可发现，同时避免“装得越多、每轮越贵”的上下文膨胀。

### 2. 用 Python 编排工具，而不是用 token 搬运中间结果

`execute_python` 是 Yuubot 的通用编排层。它不是只能计算几行数字的临时 REPL，而是运行在
Actor Workspace 中的 IPython 环境：支持 top-level `await`、同一 user turn 内的状态复用，
也能用 `asyncio.gather` 并发组合多个数据源。

```text
用户提出任务
  -> Actor 在一次 execute_python 中搜索、读取、过滤和聚合
    -> 大型中间结果留在 Python 变量或 Workspace 文件里
      -> 只把必要的摘要和最终产物交回模型
```

Python 可以直接组合 `yb` runtime facades、已启用的 `yext` integrations、MCP 能力、普通 Python
包和本地文件。实际工作中，这种方式尤其适合：

- 并发搜索多个来源，再统一去重、排序和筛选；
- 在大型 HTML 中解析 DOM、定位目标节点并一次完成修改，避免反复 `edit` 和回传全文；
- 清洗表格、分析日志、批量处理文件，只输出模型真正需要看的切片；
- 把长任务交给后台 Task，完成后再唤醒 Conversation。

中间态不必在“模型 → 工具 → 模型”的每一步反复展开，复杂任务通常能显著减少 token 消耗，
同时获得普通代码所具备的循环、并发、过滤和错误处理能力。

### 3. Workspace-native，也是 Browser-native

每个 Actor 都有自己的 Workspace。上传材料、脚本、长期项目和一次性产物都有清晰的落点；
Markdown、图片和 HTML 则可以直接从 Yuubot 的 Web UI 打开。

这对网页类产物尤其自然：Actor 写入 `artifacts/<slug>/index.html`，你用当前浏览器打开同一个
Workspace URL，继续在对话里要求修改，刷新页面就能看到新版本。不需要另起预览服务器，
也不需要再准备一个“给 Agent 用的浏览器”。HTML 可以只为这一次报告、选型、旅行计划或数据
故事而存在——真正的 HTML 日抛，同时保留 Web 平台的全部表现力。

满意后可以把文件或整个目录发布为独立快照，并设置过期时间或随时撤销。看看这个由单个 HTML
文件承载的示例：[《白昼梦の青写真》私人游玩邀请](https://share.tomorrowdawn.cc/s/sh-204af938f4c8/index.html)。

### 4. 真正的全流式渲染，为人类保留审计面

Yuubot 不会只展示一个等待动画，最后突然吐出答案。WebSocket stream 会持续传递并渲染：

- 模型文本，以及上游模型实际提供的 reasoning；
- 工具名称和正在生成的参数；
- `execute_python` 代码、Shell 命令和文件修改内容；
- 工具运行进度、stdout、结果与错误。

框架不会把工具结果替换成一个模糊的“成功”状态。工具调用与结果会进入持久 Conversation
history，刷新页面后仍可回看；stop reason、tokens、实际模型目标和 fallback 路径也会记录到
事件与 Usage 数据中。Python、Shell、读取、写入和精确编辑还有各自的专用渲染器。人类可以快速
确认 Agent 查了什么、执行了什么、改了哪里，也能在错误扩散前及时打断。

## 一个典型工作流

```text
“比较这些产品，并做一个可分享的交互式报告”
  -> Actor 根据摘要找到相关 Skill，并按需读取完整说明
    -> Python 并发收集多个来源，过滤和归一化数据
      -> 在 Workspace 生成 artifacts/comparison/index.html
        -> 你在当前浏览器里查看，并通过对话继续定向修改
          -> Yuubot 发布一份可撤销的静态快照
            -> 全部推理、工具调用和结果留在 Conversation 中供审计
```

这条路径不要求把一次性页面升级成一个正式前端项目，也不要求模型在每轮对话里重新吞下全部
资料和能力说明。

## 其他能力

- **多 Actor 与持久 Conversation**：不同 Actor 可以拥有各自的 persona、模型、Skills、
  Integrations 和 Workspace；历史记录保存在 SQLite 中。
- **OpenAI-compatible Gateway**：连接一个或多个 endpoint，以 Alias 选择模型、声明输入模态，
  并按优先级 fallback；记录 tokens、延迟和实际目标，但不臆测供应商账单。
- **按需扩展外部世界**：内置 Web、GitHub、Codex 和 OpenCode integrations；MCP 先搜索能力，
  再读取 spec 和调用，避免把所有工具定义提前铺进上下文。
- **自动化与长任务**：Cron 可持久调度 Actor 消息或 Conversation callback；后台 Task、Webhook
  和 ingress route 可以让 Agent 在网页关闭后继续工作，或由外部事件唤醒。
- **数据由自己掌握**：配置、历史、Workspace、日志、KV 和发布快照统一位于 `data_dir`；凭据由
  daemon 管理，非空 Gateway API key 加密保存且不会由 API 返回。

## 快速开始

### 1. 准备环境

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- [pnpm](https://pnpm.io/)（构建 Web UI 时需要）

### 2. 安装 Yuubot

```bash
git clone git@github.com:yuulabs/yuubot.git
cd yuubot
uv sync
```

### 3. 创建配置

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 只保存进程启动配置。Gateway、Integrations、Actors 和 Routes 在 Admin UI 中配置，
并持久化到 `data_dir` 下的数据库。默认数据目录是 `~/.yuubot-data/`。

### 4. 构建 Web UI 并启动

```bash
cd web
pnpm install
pnpm run build
cd ..
uv run ybot serve config.yaml
```

打开 <http://127.0.0.1:8765>，然后：

1. 在 **Gateway** 页面添加 OpenAI-compatible Endpoint，并创建 Alias；
2. 创建一个 Actor，选择模型并启用它；
3. 新建 Conversation，开始对话。

Endpoint API key 可以为空，因此本地模型服务也能直接接入。需要准确成本、预算、限流或供应商
治理时，请部署自己的 OpenAI-compatible gateway，再把它作为普通 Endpoint 接入 Yuubot。

## 本地数据

`data_dir` 下的主要内容：

```text
<data_dir>/db/yuubot.db           配置与持久历史
<data_dir>/workspace/<actor_id>/  Actor 工作区与 execute_python cwd
<data_dir>/logs/                  daemon 日志
<data_dir>/tmp/                   kernel 与工具临时文件
<data_dir>/kv/                    Actor KV 文档
<data_dir>/published/             对外分享的 copy-on-share 快照
```

数据库、日志轮转和磁盘告警等选项见 [`config.example.yaml`](config.example.yaml)。公网单机部署见
[`docs/server-deploy.md`](docs/server-deploy.md)。

## CLI 与开发

日常管理命令：

```bash
uv run ybot check config.yaml
uv run ybot migrate config.yaml --dry-run --json
uv run ybot status config.yaml --json
uv run ybot chat config.yaml amy "hello"
```

开发检查：

```bash
uv run ruff check src tests
uv run ty check
uv run pytest -q
cd web && pnpm run check && pnpm run build
```

开发时也可以使用 Vite dev server：

```bash
uv run ybot serve config.yaml &
cd web && pnpm run dev
```

Vite 会把 `/api` 代理到 `127.0.0.1:8765`。系统核心流程、扩展点和外部 facade 见
[`design/system-design.md`](design/system-design.md)。
