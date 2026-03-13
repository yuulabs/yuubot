---
name: mem
description: >
  记忆存储、检索与自动遗忘。
  命令: save(保存), recall(关键词/标签匹配检索，非语义搜索), show(查看标签), config(配置)。
---

# Memory Addon

## 上下文隔离

记忆按 ctx 隔离。ctx_id 由系统自动注入，**不需要手动传 --ctx**。

每条记忆有 scope：
- `private`（默认）：仅在保存时的 ctx 内可见
- `public`：所有 ctx 都可见，仅 Master 可创建

recall/show 返回的是：当前 ctx 的 private 记忆 + 所有 public 记忆。

## 可用命令

### 检索记忆
`mem recall "<words>" --tags "<tags>" --limit 10`

words 和 tags 空格分隔，匹配任意一个。至少提供一个。
使用 FTS5 全文索引匹配，支持分词。请提供具体的关键词或标签。

### 查看标签
`mem show --tags`

显示当前 ctx 可见的所有标签及其记忆数量。

### 关于关键词命中
系统会在消息中标记命中记忆的关键词。
看到命中提示时，可用 `mem recall "<关键词>"` 查看详情。
不需要每次都 recall，只在觉得有用时查。

### 保存记忆
`mem save "<content>" --tags <tag1>,<tag2>,... [--scope private|public]`

content 不宜过长（默认 < 500 字）。tags 可选。scope 默认 private，public 需 Master 权限。

### 配置
`mem config --forget-days <days>`

设置记忆保留天数（默认 90 天）。
