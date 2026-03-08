# Skills 详细设计

Skills 是 CLI 工具，Agent 通过 `execute_skill_cli("ybot <skill> <command> ...")` 调用。每个 skill 安装时会在 `~/.yagents/skills/<skill_name>/` 下生成 SKILL.md，Agent 通过 prompt 注入了解用法。

## im — 即时消息

### send

```bash
ybot im send '<msg_json>' --ctx <ctx_id>
ybot im send '<msg_json>' --uid <user_id>
ybot im send '<msg_json>' --gid <group_id>
```

- `msg_json`：消息段 JSON 数组，格式见消息格式定义
- `--ctx`：发送到 ctx_id 对应的群聊/私聊
- `--uid`：直接指定 QQ 号（私聊）
- `--gid`：直接指定群号（群聊）

三者互斥，优先级：`--ctx` > `--uid` > `--gid`

**实现**：
1. 解析 msg_json 为内部消息模型
2. 如果使用 `--ctx`，查询 ctx_id 映射获取目标
3. 将内部消息模型转换为 OneBot V11 格式
4. 调用 Recorder HTTP API → NapCat HTTP API 发送

### search

```bash
ybot im search "<keywords>" --ctx <ctx_id> [--limit 20] [--days 7]
```

- `keywords`：空格分隔的关键词列表，匹配任意一个即返回
- `--ctx`：限定搜索范围（可选，不指定则全局搜索）
- `--limit`：返回条数上限（默认 20）
- `--days`：搜索最近 N 天的消息（默认 7）

**实现**：
1. 从 SQLite 查询匹配的消息
2. 使用 SQL LIKE 或 FTS5 全文搜索
3. 返回格式化的消息列表（时间、发送者、内容摘要）

**输出格式**：
```
[2024-01-15 14:30] user_123 (ctx 5): 今天天气真好
[2024-01-15 14:32] user_456 (ctx 5): 是啊，适合出去玩
共找到 2 条消息
```

### list

```bash
ybot im list friends              # 好友列表
ybot im list groups               # 群聊列表
ybot im list members --gid <gid>  # 群成员列表（限制人数）
ybot im list contexts             # 所有已知 ctx 列表
```

**实现**：调用 Recorder HTTP API → NapCat API 获取列表信息。

## web — 网络功能

### search

```bash
ybot web search "<query>" [--limit 5]
```

- `query`：搜索关键词
- `--limit`：返回结果数（默认 5）

**实现**：调用 Tavily API 搜索。

**输出格式**：
```
1. [标题] URL
   摘要...

2. [标题] URL
   摘要...
```

### read

```bash
ybot web read "<url>" [--summary]
```

- `url`：目标网页 URL
- `--summary`：只返回摘要（截断到合理长度）

**实现**：基于 `agent_read.py` 的 Playwright + Trafilatura 方案。
1. 使用持久化浏览器 profile（复用登录态）
2. Playwright 加载页面
3. Trafilatura 提取正文
4. 输出 Markdown 格式的正文

### download

```bash
ybot web download "<urls>" <folder>
```

- `urls`：多行字符串，每行一个 URL
- `folder`：本机下载目标文件夹

**实现**：
1. 解析 URL 列表
2. 并发下载（aiohttp 或 Playwright）
3. 保存到指定文件夹
4. 输出下载结果（成功/失败/文件路径）

## mem — 记忆系统

### 上下文隔离

记忆按 ctx 隔离，防止跨群/跨私聊信息泄露。

- `ctx_id` 由系统自动从环境变量 `YUU_BOT_CTX` 注入，CLI 不再接受 `--ctx` 参数
- 每条记忆有 `scope` 字段：
  - `private`（默认）：仅在保存时的 ctx 内可见
  - `public`：所有 ctx 都可见，仅 Master 可创建
- recall/show 的可见范围 = `(ctx_id=当前 AND scope=private) OR scope=public`
- 无 ctx 时只能看到 public 记忆

### save

```bash
ybot mem save "<content>" --tags <tag1>,<tag2>,... [--scope private|public]
```

- `content`：记忆内容（不宜过长，建议 < 500 字）
- `--tags`：逗号分隔的标签，用于分类
- `--scope`：`private`（默认）或 `public`（需 Master 权限）

**实现**：
1. 从 `YUU_BOT_CTX` 读取 ctx_id
2. 生成唯一 mem_id，记录创建时间和最后访问时间
3. 写入 SQLite `memories` 表（含 scope 字段）

**输出**：
```
已保存记忆 [mem_id: 42]，标签: preference, food，scope: private
```

### recall

```bash
ybot mem recall "<words>" [--tags "<tags>"] [--limit 10]
```

- `words`：空格分隔的关键词，匹配任意一个
- `--tags`：空格分隔的标签，匹配任意一个
- `--limit`：返回条数上限（默认 10）

words 和 tags 至少提供一个。

**实现**：
1. 从 `YUU_BOT_CTX` 读取 ctx_id
2. 查询 `(ctx_id=当前 AND scope=private) OR scope=public` 的匹配记忆
3. 更新匹配记忆的 `last_accessed` 时间（延长生命周期）
4. 返回记忆列表

**输出格式**：
```
[mem 42] (tags: preference, food) 2024-01-15
  用户张三喜欢吃川菜，不吃辣

共找到 1 条记忆
```

### delete

```bash
ybot mem delete <ids>
```

- `ids`：逗号分隔的记忆 ID

**输出**：
```
已删除 3 条记忆: 42, 58, 73
```

### show

```bash
ybot mem show --tags
```

显示当前 ctx 可见的所有标签及其记忆数量。

**输出**：
```
标签列表 (ctx 5):
  preference: 12 条
  food: 5 条
```

### 自动遗忘

- 默认保留期：90 天（3 个月）
- 判断依据：`last_accessed` 时间（每次 recall 命中会刷新）
- 清理时机：Recorder 启动时 + 每日定时清理
- 可配置：`ybot mem config --forget-days <days>`

## Skill 安装

```bash
ybot skills install <skill_name>
```

安装流程：
1. 安装 skill 所需的 Python 依赖到当前虚拟环境
2. 生成 SKILL.md 到 `~/.yagents/skills/<skill_name>/`
3. SKILL.md 包含 skill 的用法文档，Agent 通过 prompt 注入了解如何调用

### SKILL.md 示例（im）

```markdown
---
name: im
description: QQ 即时消息收发与搜索
---

# IM Skill

## 可用命令

### 发送消息
\`ybot im send '<msg_json>' --ctx <ctx_id>\`

msg_json 格式：
\`[{"type":"text","text":"你好"},{"type":"at","qq":"123456"}]\`

### 搜索消息
\`ybot im search "<keywords>" --ctx <ctx_id> --limit 20 --days 7\`

### 列表查询
\`ybot im list friends\`
\`ybot im list groups\`
\`ybot im list members --gid <group_id>\`
\`ybot im list contexts\`
```
