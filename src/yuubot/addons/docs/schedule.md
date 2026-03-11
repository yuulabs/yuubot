---
name: schedule
description: >
  定时任务管理：创建、查看、修改、删除 cron 定时任务。
  任务触发时拉起指定 agent 执行。
---

# Schedule Addon

## 可用命令

### 创建定时任务
`schedule create "<cron>" "<task>" --agent <name> --ctx <id> --recurring`

- `cron`: 标准 5 字段 cron 表达式（分 时 日 月 周）
- `task`: 触发时发给 agent 的任务描述
- `--agent`: 执行任务的 agent 名称（默认为你自己，只能指定自身或 subagents 中的 agent）
- `--ctx`: 可选的目标 context ID（发消息时需要）
- `--recurring`: 重复执行。**默认为一次性任务**，触发一次后自动禁用

示例:
- 一次性提醒（默认）: `schedule create "30 14 15 2 *" "提醒用户开会" --ctx 3`
- 每天早上 9 点（重复）: `schedule create "0 9 * * *" "发送天气播报到群里" --ctx 3 --recurring`
- 每周一上午 10 点（重复）: `schedule create "0 10 * * 1" "发送周报提醒" --ctx 5 --recurring`

### 查看定时任务
`schedule list`          — 仅显示活跃（enabled）任务
`schedule list --all`    — 显示全部任务（含已禁用）

### 修改定时任务
`schedule update <id> --cron "..." --task "..." --agent <name> --enable/--disable`

所有选项均可选，只更新提供的字段。

### 删除定时任务
`schedule delete <id>`

## 长周期限制

月份字段未覆盖全部 12 个月的 **周期性任务** 被视为长周期任务（如季度/年度），
数量受限（默认上限 5）。一次性任务不受此限制。

## 注意事项

- cron 表达式是 UTC+8 时间
- 创建后自动通知 daemon 加载，无需重启
- daemon 未运行时创建的任务会在下次启动时自动加载
- 如需发消息，记得带 `--ctx` 参数
