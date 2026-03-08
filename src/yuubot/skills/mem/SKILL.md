---
name: mem
description: >
  记忆存储、检索与自动遗忘。
  命令: save(保存), recall(关键词/标签匹配检索，非语义搜索), delete(删除), show(查看标签), config(配置)。
  首次使用前请先 cat 本文件查看完整参数格式。
---

# Memory Skill

## 上下文隔离

记忆按 ctx 隔离。ctx_id 由系统自动注入（环境变量 YUU_BOT_CTX），**不需要手动传 --ctx**。

每条记忆有 scope：
- `private`（默认）：仅在保存时的 ctx 内可见
- `public`：所有 ctx 都可见，仅 Master 可创建

recall/show 返回的是：当前 ctx 的 private 记忆 + 所有 public 记忆。

## 可用命令

### 保存记忆
`ybot mem save "<content>" --tags <tag1>,<tag2>,... [--scope private|public]`

content 不宜过长（默认 < 500 字）。tags 可选。scope 默认 private，public 需 Master 权限。

### 检索记忆
`ybot mem recall "<words>" --tags "<tags>" --limit 10`

words 和 tags 空格分隔，匹配任意一个。至少提供一个。
使用 FTS5 全文索引匹配，支持分词。请提供具体的关键词或标签。

### 删除记忆
`ybot mem delete <ids>`

ids 是逗号分隔的记忆 ID。仅 Master 可执行。

### 查看标签
`ybot mem show --tags`

显示当前 ctx 可见的所有标签及其记忆数量。

### 配置
`ybot mem config --forget-days <days>`

设置记忆保留天数（默认 90 天）。

## 使用指引

### 何时保存
- 用户明确表达的偏好（喜好、习惯）
- 用户分享的重要个人信息（生日、职业等）
- 用户明确要求你记住的事情

### 何时不保存
- 一次性闲聊
- 已存在的重复记忆（先看关键词命中提示）
- 敏感隐私信息（密码、身份证号等）

### 保存技巧
- 简洁陈述句概括，不要原文照搬
- 标签用通用分类词：preference, person, event, topic

### 关于关键词命中
系统会在消息中标记命中记忆的关键词。
看到命中提示时，可用 `ybot mem recall "<关键词>"` 查看详情。
不需要每次都 recall，只在觉得有用时查。

### 权限
- delete 仅限 Master 使用
- save --scope public 仅限 Master 使用
