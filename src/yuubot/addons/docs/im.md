---
name: im
description: >
  QQ 即时消息收发、搜索与浏览。你可以使用该工具了解聊天上下文。
  命令: send(发送消息), search(关键词全文匹配，非语义搜索), browse(按msg_id或时间范围浏览上下文), list(好友/群/成员列表)。
---

# IM Addon

## 可用命令

### 发送消息

```
im send --ctx <ctx_id> -- <msg_json>
```

`--` 后面的 JSON 会被自动解析，不需要转义。

msg_json 是一个 JSON 数组，每个元素是一个消息段:
- 文本: `{"type":"text","text":"内容"}`
- @提及: `{"type":"at","qq":"<qq号>"}`  ← qq 号即消息 XML 中 `<msg qq="...">` 的值
- 图片: `{"type":"image","url":"<url>"}`

示例（混合消息）:
```
im send --ctx 3 -- [{"type":"text","text":"吃饭啦！"},{"type":"at","qq":"123456"},{"type":"text","text":" 快来～"}]
```

也可以用 `--uid <user_id>` 或 `--gid <group_id>` 代替 `--ctx`。
三者互斥，优先级: --ctx > --uid > --gid

可选参数: `--delay <秒数>` — 等待指定秒数后再发送。

⚠️ 要 @某人时**必须**使用 at 段，不要在 text 里写 `@xxx`，否则对方不会收到提醒。

**群聊限流**: 同一群聊每分钟最多发送 5 条消息。超出后发送被拒绝。
返回值包含 `剩余额度: N/5`，表示当前窗口内还可发送的消息数。
⚠️ 当剩余额度为 0 时，请使用 `--delay 60` 等待限流窗口重置后再发送。
qq不会渲染md文本。因此请使用纯文本配合合理的空白符进行排版。

### 搜索消息
`im search "<keywords>" --ctx <ctx_id> --limit 20 --days 7`

keywords 是空格分隔的关键词，使用 SQLite FTS5 全文匹配，匹配任意一个即返回。
⚠️ 纯关键词匹配，不支持按 msg_id/user_id 筛选，不支持语义搜索。
按 msg_id 定位上下文 → 用 browse --msg；按时间范围 → 用 browse --since --until。

### 浏览消息
`im browse --msg <msg_id> --before 10 --after 10`
`im browse --ctx <ctx_id> --since "2024-01-01" --until "2024-01-31" --limit 50`

浏览指定消息前后的上下文，或按时间范围浏览会话消息。

### 关于 msg_id

你在 `<msg id="...">` 输出和 `[回复:...]` 中看到的 ID。直接使用这些 ID 即可。

### 列表查询
`im list friends` — 好友列表
`im list groups` — 群聊列表
`im list members --gid <group_id>` — 群成员列表
`im list contexts` — 所有已知 ctx 列表
