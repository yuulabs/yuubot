# 运维手册

日常操作、日志排查、进程管理。开发前先看 `design.md`，出故障了看这里。

---

## 进程管理

三个独立进程，各自生命周期互不影响：

| 进程 | 职责 | 重启代价 |
|------|------|----------|
| NapCat | QQ 登录保活 | 重启需重新扫码 |
| Recorder | 落盘 + 转发 | 无损，消息不会丢 |
| Daemon | Agent 驱动 | 可随时重启 |

```bash
# 启动（NapCat + Recorder 后台 screen，Daemon 前台）
ybot launch        # 启动 NapCat + Recorder
ybot up            # 启动 Daemon（前台，Ctrl+C 停止）

# 停止
ybot down          # 停止 Daemon
ybot shutdown      # 停止 Recorder + NapCat

# 查看后台进程日志
screen -r napcat      # NapCat 实时日志
screen -r recorder    # Recorder 实时日志
```

---

## 日志系统

日志文件位于 `~/.yuubot/logs/`（可在 `config.yaml` 的 `log_dir` 字段修改）。

```
~/.yuubot/logs/
  yuubot.log          # 当前日志（DEBUG+，完整细节）
  yuubot.log.1.gz     # 轮转归档（每 20MB 轮转，保留 5 份）
  yuubot.log.2.gz
  ...
```

**两个输出通道：**
- **stderr（控制台）**：INFO 及以上，彩色紧凑格式，关键事件才出现
- **文件**：DEBUG 及以上，带精确时间戳（毫秒级），全量细节

**日志格式（文件）：**
```
2026-03-11 14:23:01.234 I dispatcher | event: type=group user=12345 group=67890 ctx=5
2026-03-11 14:23:01.891 I agent_runner | agent start: ctx=5 agent=main task_id=abc123def... continuation=False
2026-03-11 14:23:08.442 I agent_runner | agent done: ctx=5 agent=main task_id=abc123def... tokens=1842
```

---

## 按 ctx_id 排查

`ctx_id` 是内部整数，对应一个聊天上下文（私聊/群聊）。所有关键日志都带 `ctx=N`。

```bash
# 查看某个 ctx 的所有系统行为
grep "ctx=5" ~/.yuubot/logs/yuubot.log

# 带时间范围过滤（查 14 点的记录）
grep "ctx=5" ~/.yuubot/logs/yuubot.log | grep "^2026-03-11 14:"

# 实时跟踪某个 ctx
tail -f ~/.yuubot/logs/yuubot.log | grep "ctx=5"

# 查看 ctx_id 与实际聊天的对应关系（从数据库）
.venv/bin/python -c "
import asyncio
from yuubot.config import load_config
from yuubot.core.db import init_db
from yuubot.core.models import ContextEntry
async def main():
    cfg = load_config()
    await init_db(cfg.database.path)
    for e in await ContextEntry.all().order_by('ctx_id'):
        print(f'ctx={e.ctx_id} type={e.ctx_type} target={e.target_id}')
asyncio.run(main())
"
```

---

## 按 task_id 关联 traces

每次 agent run 都会记录 `task_id`（32 位 hex），与 yuuagents traces DB 里的 conversation_id 对应。

```bash
# 从日志找 task_id
grep "ctx=5" ~/.yuubot/logs/yuubot.log | grep "agent start"
# 输出示例：... agent start: ctx=5 agent=main task_id=abc123def456... continuation=False

# 用 task_id 在 traces DB 里查完整对话
.venv/bin/python scripts/conv.py <task_id>

# task_id 前缀也能查（conv.py 支持前缀匹配）
.venv/bin/python scripts/conv.py abc123de
```

**scripts/conv.py 用法：**
```bash
# 列出最近对话（agent, model, 轮数, tool 调用数）
.venv/bin/python scripts/conv.py

# 完整对话（含 tool 输出）
.venv/bin/python scripts/conv.py <conversation_id>

# 紧凑视图（只看 assistant 文本 + tool 调用，隐藏输出）
.venv/bin/python scripts/conv.py <conversation_id> -n

# 指定 DB 路径
.venv/bin/python scripts/conv.py --db ~/.yagents/traces.db <conversation_id>
```

输出格式：
- `[USER]` — 传入的 QQ 消息，含 ctx/group 上下文
- `[ASSISTANT]` — LLM 文本输出和 tool 调用（`→ tool_name(args)`）
- `[TOOL: name]` — tool 结果（截断至 600 字符）

---

## 常见故障

### traces 未记录，只能靠日志排查

traces DB (`~/.yagents/traces.db`) 需要 `ytrace server` 在运行时才会写入。如果 traces 为空：

1. 先从日志确认 agent 是否被触发：`grep "agent start" ~/.yuubot/logs/yuubot.log`
2. 用 ctx_id 追踪完整消息路径（dispatch → session → agent run）
3. 看 session 状态：grep `Session created/expired/closed`
4. 看 dispatcher 的 `should_respond` 判断结果

### NapCat 断连

```bash
screen -r napcat    # 查看 NapCat 日志
ybot shutdown && ybot launch  # 重启 NapCat + Recorder
```

### Daemon 无响应

```bash
ybot down && ybot up    # 重启 Daemon（Recorder 不受影响，消息不丢）
```

### 查看当前 session 状态

Daemon 暴露 `/health` 端点：
```bash
curl http://127.0.0.1:8780/health
# {"status":"ok","workers":2}  # workers = 活跃的 per-ctx 队列数
```
