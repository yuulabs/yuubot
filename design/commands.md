# 命令系统与权限设计

借鉴旧 qqbot 项目的命令系统设计，全新实现。

## Role 权限系统

权限从高到低：

| Role | 说明 | 来源 |
|------|------|------|
| **Master** | 最高权限，可使用一切命令 | 由 config.yaml 配置 QQ 号 |
| **Mod** | 管理员，可管理群聊 bot 行为 | 由 Master 通过 `/bot grand` 授权 |
| **Folk** | 默认角色，可使用普通命令 | 所有人的默认角色 |
| **Deny** | 被禁止使用 Bot | 由 Mod/Master 设置 |

```python
from enum import IntEnum

class Role(IntEnum):
    DENY = 0
    FOLK = 1
    MOD = 2
    MASTER = 3
```

权限存储在 SQLite 中，格式：`(user_id, role, scope)`。scope 为 `global` 或 `group_id`。

## 响应模式

| 模式 | 说明 | 默认适用 |
|------|------|----------|
| **at** | 必须 @bot 才响应 | 群聊中除 Master 外的所有人 |
| **free** | 只要输入符合命令格式就处理 | Master 私聊 |

- Master 默认 free 模式
- 群聊默认 at 模式，Master 可通过 `/bot on --free` 开启 free 模式
- 私聊默认不响应，需要 `/bot allow-dm @user` 开启

## 命令树

### 树形结构

```python
class Command:
    prefix: str                    # 命令前缀
    subs: list[Command]            # 子命令
    executor: Executor | None      # 叶子节点的执行器
    min_role: Role                 # 最低权限要求
    help_text: str                 # 帮助文本

class RootCommand(Command):
    """根命令，特殊处理 / 开头的消息"""
    entries: list[str]             # 入口前缀列表 ["/y", "/yuu"]
```

### 匹配算法

```
输入: "/yhelp"
  1. RootCommand 检测到 /y 入口前缀，去掉 → "help"
  2. 在子命令中查找最长前缀匹配 → help_cmd
  3. help_cmd 是叶子节点 → 调用 executor
  
输入: "/ybot grand @user mod"
  1. RootCommand 去掉 /y → "bot grand @user mod"
  2. 匹配 bot_cmd → 剩余 "grand @user mod"
  3. 匹配 grand_cmd → 剩余 "@user mod"
  4. grand_cmd 是叶子节点 → 调用 executor，参数为 "@user mod"
```

### 匹配结果

```python
@dataclass
class MatchResult:
    command: Command               # 匹配到的命令
    remaining: str                 # 剩余未匹配的文本（作为参数）
    entry: str                     # 使用的入口前缀
```

## 入口映射

默认入口：`/y`, `/yuu`

预处理规则：遇到 `/<entry>` 时，去掉 entry 前缀。例如：
- `/yhelp` → `/help`
- `/yuuhelp` → `/help`
- `/ybot on` → `/bot on`

可通过 `/bot set <entry> <command_route>` 新增入口映射。

## 内置命令

### Master-Only

| 命令 | 格式 | 说明 |
|------|------|------|
| grand | `/bot grand @user <role> [--unlimited]` | 变更用户角色。默认仅当前群聊，`--unlimited` 跨群聊 |
| free | `/bot on --free` | 开启 free 模式（群聊中不需要 @bot） |
| allow-dm | `/bot allow-dm @user` | 允许特定用户私聊触发 bot |

### Mod-Only

| 命令 | 格式 | 说明 |
|------|------|------|
| on/off | `/bot on` / `/bot off` | 在群聊中打开/关闭 bot |
| grand | `/bot grand @user folk/deny` | Mod 只能授权 Folk 或 Deny |
| set | `/bot set <entry> <command_route> [--unlimited]` | 新增入口映射（`--unlimited` 需 Master） |

### Folk

| 命令 | 格式 | 说明 |
|------|------|------|
| help | `/help [route...]` | 逐层浏览命令树。显示当前命令的详情 + 下一层子命令的摘要（不递归展开） |
| llm | `/llm <content>` | 触发 Agent 回答问题（核心命令） |

### 命令风格

```
/<head> route route?param opt --params --params content
```

- route 不允许带空格
- 命令各部分用空格分隔
- content 包括后面的文本，也包括引用/回复的消息内容

## Help 命令行为

`/help [route...]` 采用逐层浏览模式，每次只展示当前层的信息：

1. **当前命令详情**：help_text、所需权限、用法格式
2. **下一层子命令摘要**：每个子命令只显示 prefix + 一句话说明，不递归展开

示例：

```
/yhelp
→ 显示根命令信息 + 一级命令摘要：
    bot   - Bot 管理命令
    help  - 查看帮助
    ask   - 触发 Agent

/yhelp bot
→ 显示 bot 命令详情 + 二级命令摘要：
    grand    - 变更用户角色
    on       - 开启 bot
    off      - 关闭 bot
    set      - 管理入口映射
    allow-dm - 允许私聊

/yhelp bot grand
→ 显示 grand 命令完整详情（叶子节点，无子命令摘要）：
    用法: /bot grand @user <role> [--unlimited]
    权限: Mod
    ...
```

## Agent 触发命令

核心命令 `/llm` 是触发 Agent 的主要方式：

```
/yllm 今天天气怎么样？
/yllm 帮我搜索一下 Python 3.14 的新特性
/yllm 总结一下群里最近讨论了什么
```

bot会解析该命令，然后创建一个Agent（使用yuuagents）工作。

## 命令注册

命令树在 daemon 启动时构建：

```python
# builtin.py
def build_command_tree(config) -> RootCommand:
    # 管理命令
    grand_cmd = Command(prefix="grand", executor=GrandExecutor(), min_role=Role.MOD)
    on_cmd = Command(prefix="on", executor=OnExecutor(), min_role=Role.MOD)
    off_cmd = Command(prefix="off", executor=OffExecutor(), min_role=Role.MOD)
    set_cmd = Command(prefix="set", executor=SetExecutor(), min_role=Role.MOD)
    allow_dm_cmd = Command(prefix="allow-dm", executor=AllowDmExecutor(), min_role=Role.MASTER)
    bot_cmd = Command(prefix="bot", subs=[grand_cmd, on_cmd, off_cmd, set_cmd, allow_dm_cmd])
    
    # 用户命令
    help_cmd = Command(prefix="help", executor=HelpExecutor(), min_role=Role.FOLK)
    ask_cmd = Command(prefix="ask", executor=AskExecutor(), min_role=Role.FOLK)
    
    # 根命令
    root = RootCommand(
        subs=[bot_cmd, help_cmd, ask_cmd],
        entries=config.entries,  # ["/y", "/yuu"]
    )
    return root
```
