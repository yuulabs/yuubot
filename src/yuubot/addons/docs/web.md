---
name: web
description: >
  网页搜索、阅读与下载（真实浏览器）。
  命令: search(Tavily搜索，每任务限3次), read(提取网页正文), download(下载文件)。
---

# Web Addon

## 可用命令

### 搜索
`web search "<query>" --limit 5`

使用 Tavily 搜索引擎。

**搜索限流**: 每个任务最多搜索 3 次。超出后搜索被拒绝。
返回值包含 `剩余搜索额度: N/3`，表示当前任务内还可搜索的次数。

### 阅读网页
`web read "<url>" --summary`

提取网页正文内容，--summary 截断到合理长度。如果你知道URL, 优先使用该方法。注意，URL必须包含完整的协议头（如 https://）。

### 下载文件
`web download "<urls>" <folder>`

urls 可以是多行字符串，每行一个 URL。folder 是本机下载目标文件夹。
