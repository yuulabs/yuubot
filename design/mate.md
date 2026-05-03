# Mate 设计（精简版）

## 核心抽象

Mate 是"拥有持久化人格的具名实体"。对外永远在线，内部在需要时拉起一个 Agent turn，turn 结束后 Agent 消亡，持久状态保留。

**Mate = Character + 持久化层**

- **Character**：决定人格和行为
- **workspace**：磁盘目录，跨 turn 持久。重要状态落盘于此，不依赖内存。
- **对话历史/内存持久态**：Lineage维持。

只有一个 Mate：`Shiori`（#0），系统启动时自动创建。不需要多 Mate 动态创建。

### 信箱

Shiori 有一个入站信箱内容是标准list[yuullm.Message]

Shiori 有一个简单的消费者loop持续从信箱中拉取消息并激活Lineage中的Agent处理。

### 消费模型：iteration 边界

```
for _ in steps:
  if llmstep:
    handle 
```


## 系统注入消息

Agent 不应该在系统提示里被要求"记得存盘"——要么忘，要么过度存，要么存盘操作干扰正事。也不该让另一个没有上下文的 agent 来打扫——会误删或错误修改。

Mate 的做法：系统在合适的时机 fork 当前 agent（携带完整 history），追加一条 system 消息，让 agent 自行处理。不同场景的触发条件、消息内容、返回值处理不同：

| 场景 | 触发条件 | system 消息 | 返回值处理 |
|---|---|---|---|
| **维护** | idle 超过阈值 | "总结工作要点，整理 workspace，更新笔记" | 不关心文本返回值。agent 通过 tool call 写盘即可。 |
| **rollover** | 上下文接近上限 | "总结这段对话。记录 kernel 中仍活跃的 Python 变量。" | 捕获 agent 最终文本回复，作为新 turn 的起始上下文。 |
| **超时** | delegate_to_opencode 超时 | 由 system 往信箱投 timeout 通知，Shiori 下个 iteration 自行决定 | — |

核心优势：agent 在任何场景都持有完整上下文，不会做出脱离实际的决策。

## 编码委托：delegate_to_opencode

Shiori 是完整的 Agent，有自己的推理能力和工具。**仅在遇到编码任务时**委托 opencode——不自己写代码。

```
人类 → Shiori 理解意图，自行处理非编码任务（对话、分析、管理等）
     → 遇到编码需求时，Shiori 调 delegate_to_opencode(ssh, task, context)
     → opencode 完成编码（探索、编写、测试、lint）→ 返回结果
     → Shiori 整合结果，继续后续工作
```

`delegate_to_opencode` 是 Shiori 的工具之一，不是唯一的工具。底层对本地 opencode 还是远程 GPU 节点透明。

### 远程自举

```python
def configure_opencode(ssh):
    """
    ssh: bot 自己通过 paramiko 连接的对象
    
    在远程机器上自举 opencode 环境：
    1. 检测并安装 opencode
    2. 同步 ~/.local/share/opencode/auth.json
    3. 同步项目 skills（.opencode/skills/）
    4. 安装标准 agent（denoiser）
    """
```

### 标准 opencode agent 阵容

bot 在 `configure_opencode` 时写入 `.opencode/agents/`：

- **build**（内置）：全权限开发
- **plan**（内置）：只读分析，不改文件
- **explore**（内置）：只读探索代码库
- **denoiser**（自定义）：专门整理代码——去重、简化、统一风格，不改变行为。跑测试验证。

### 编码流水线

```python
# bot 编排两步，避免屎山积累
ssh.exec_command("opencode run --agent build '{task}'")
ssh.exec_command("opencode run --agent denoiser 'Clean up the code just written. Run tests.'")
```

### Skills 沉淀

bot 可以将 explore 的发现沉淀为 opencode skill，免去每次重新探索：

```python
ssh.exec_command("mkdir -p .opencode/skills/{name}")
ssh.write_file(".opencode/skills/{name}/SKILL.md", skill_md)
# opencode 下次启动时自动发现
```

### 超时

`delegate_to_opencode` 支持 deadline。超时后 system 往信箱投一条 timeout 通知，Shiori 在下一 iteration 看到并决定重试或上报。

## 可观测性

跟踪 opencode 任务的状态，由 runtime 自动维护：

```
status: idle | running | error
frontier: 当前执行的 tool call（名称、输入摘要、已执行时长）
last_heartbeat: 最近完成时间
error_info: 崩溃信息
```

直接复用 opencode session 的 event stream（SSE），不做重复建设。

## 参考：OpenCode CLI

> 文档: <https://opencode.ai/docs> · CLI: <https://opencode.ai/docs/cli> · Server/SDK: <https://opencode.ai/docs/server> <https://opencode.ai/docs/sdk>

```bash
# 一次性任务
opencode run "task description"
opencode run --model anthropic/claude-sonnet-4-6 "task"
opencode run --agent build "task"
opencode run --agent plan "analyze only, no edits"
opencode run --continue                  # 继续上次会话
opencode run --session abc123 "task"     # 恢复到指定会话
opencode run --format json "task"        # JSON 输出

# 持久 server（API 模式）
opencode serve --port 4096              # 启动 HTTP server
# SDK: npm install @opencode-ai/sdk
# client.session.create() → client.session.prompt() → 结果

# Agent 管理
opencode agent list                      # 列出所有 agent
opencode agent create                    # 交互式创建 agent
# 或直接写文件: .opencode/agents/<name>.md

# Skills 路径（OpenCode 自动发现）
# .opencode/skills/<name>/SKILL.md
# ~/.config/opencode/skills/<name>/SKILL.md
# .claude/skills/<name>/SKILL.md         (兼容)
# .agents/skills/<name>/SKILL.md         (兼容，yuubot 已有)
```

常用 agent 配置模板（`.opencode/agents/denoiser.md`）：

```yaml
---
description: Code cleanup specialist. Refactors for clarity without changing behavior.
mode: subagent
permission:
  edit: allow
  bash: { "*": allow }
model: anthropic/claude-sonnet-4-6
---
You are a code denoiser. Take working code and make it clean.
- Never change behavior. Run tests before and after.
- Eliminate duplication. Simplify control flow.
- Remove dead code, stale comments, debug prints.
```

## 与旧版设计的差异

| 旧版 | 精简版 | 原因 |
|---|---|---|
| 多 Mate 互发消息 | 单 Mate，单入站信箱 | multi-agent 协调不如 solo frontier model |
| 消息等到最终文本才消费 | iteration 边界注入 | tool chain 可能很长，边执行边收消息更快 |
| 信箱消息含 reply_to / ticket_id 路由 | 简化为 from + content + timestamp | 不需要多 Mate 路由 |
| 内部 Agent lineage | 全部砍掉。自我维护用 idle fork，编码委托 opencode | curator/sweeper 无上下文易犯错；openode 内置 agent 够用 |
| Mem curator | 被 idle 自我维护替代 | 同上下文 agent 自行整理，比无上下文 curator 可靠 |
| Profile → 模型路由 | 砍掉 | v2，等模型阵容稳定再说 |
| create_mate / remove_mate / send_to_mate API | 砍掉 | 不需要多 Mate |
