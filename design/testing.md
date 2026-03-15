# 测试设计

## 核心原则

只测两个稳定界面，不测中间实现。

### 界面 1：用户侧（bot 回复了什么）

入口：`send(dispatcher, event)` → 断言 `sent_texts()` 的回复内容。

这是用户唯一能观察到的东西。路由怎么走、状态机怎么转、builder 怎么拼——都是实现细节，重构时会变，但用户看到的回复不该变。

### 界面 2：LLM 侧（LLM 收到了什么）

入口：同上 → 断言 mock LLM 收到的 messages 列表。

LLM 有概率性。我们能控制的只有"喂给它什么"。很多功能的正确性归结为：**如果 LLM 获得了足够的信息，它会做正确的事**。所以必须验证 LLM 实际收到的渲染文本——用户消息是否完整、system prompt 是否包含必要上下文、工具定义是否正确、历史是否被正确拼接。

### 不测的部分

路由、ConversationManager 状态机、AgentRunBuilder 拼装、render 内部 XML 结构、summarizer prompt 模板、Session.fork/spawn、日志格式——这些全是中间实现。如果它们出了 bug，一定会在上述两个界面之一暴露出来。

例外：复现 bug 时可以写临时的中间层测试，修复后决定是否保留。

## Mock 策略

只 mock 外部依赖：

| 依赖 | Mock 方式 | 原因 |
|------|-----------|------|
| Recorder API | respx | 网络调用，不可控 |
| LLM provider | patch `stream()` | 成本+不确定性 |
| 第三方 API (hhsh 等) | respx | 网络调用 |

DB 用真实临时 SQLite，不 mock。

## LLM mock 增强

当前 `mock_llm()` 只捕获"返回什么"。需要增强为同时捕获"收到什么"：

```python
@dataclass
class LLMCapture:
    """捕获 LLM 调用的输入和输出"""
    calls: list[LLMCall]  # 每次调用的 messages + response

@dataclass
class LLMCall:
    messages: list[dict]   # LLM 实际收到的 messages
    tools: list[dict]      # LLM 可用的 tools
    response: ...          # 我们让它返回的内容
```

这样测试可以断言：
- `llm_capture.calls[0].messages` 包含用户消息
- system prompt 包含某个关键上下文
- tools 列表包含/不包含某个 capability

## 场景矩阵

### 命令系统
- `/yhelp` → 用户收到命令列表
- 未知命令 → 用户收到提示
- folk 执行 mod 命令 → 被拒绝
- `/ybot grant` 提权后 → 可执行
- `/ybot off` → 非 master 消息被忽略；master `/ybot on` 恢复
- `/ybot on --free` → 群消息无需 @bot

### 对话生命周期
- `@bot 你好` → 用户收到回复 + LLM 收到正确 messages
- 会话中 `@bot 继续` → LLM 收到完整历史（含上一轮）
- `/yclose` → 用户收到关闭确认
- 会话空闲超时 → 新消息开新会话（LLM 不含旧历史）
- 运行中连发多条 → LLM 收到合并后的 pending messages

### 私聊
- 未授权用户私聊 → 无回复
- master `/ybot allow-dm` 后 → 可私聊
- master 私聊 → 始终可用

### 多角色 agent
- `/y#general 问题` → LLM 收到 general 的 system prompt（而非 main 的）
- folk 用 master-only agent → 被拒绝
- folk 父不能 spawn master 子 → 配置时报错

### 能力调用
- LLM 返回 tool call → Recorder API 收到正确请求
- 被 exclude 的 action → tool call 返回错误

### 长时任务
- 工具超时 → 用户收到进度提示
- delegate 完成 → 结果回传

### 契约测试（保留）
- 每个叶命令有 spec 声明
- min_role 一致

## 文件结构

```
tests/
├── conftest.py                      # 共享 fixtures
├── mocks.py                         # LLM/Recorder/API mock（增强 LLMCapture）
├── helpers.py                       # 断言工具
├── contracts/
│   └── test_commands.py             # 命令契约
└── flows/
    ├── test_admin_management.py     # bot 开关、grant
    ├── test_agent_permissions.py    # 角色权限、权限不升级
    ├── test_llm_session.py          # 对话创建、续写、关闭、rollover
    ├── test_private_chat.py         # 私聊白名单
    ├── test_free_mode.py            # free 模式
    ├── test_soft_timeout.py         # 超时与子任务
    ├── test_group_discovery.py      # 帮助、hhsh
    ├── test_conversation_lifecycle.py  # 超时过期、pending 合并
    └── test_capability_calls.py       # LLM tool call → 能力执行
```

## 要删除的测试

根目录下所有 `tests/test_*.py`：

- `test_routing.py` — 内部路由函数
- `test_conversation.py` — 状态机方法
- `test_builder.py` — builder 拼装
- `test_agent_runner.py` — Session 内部 API
- `test_render.py` — XML 快照
- `test_llm_executor.py` — 私有方法
- `test_summarizer.py` — prompt 模板
- `test_forward_logging.py` — 日志格式
- `test_recorder_server.py` — 日志格式
- `test_cli_permissions.py` — CLI 注册
- `test_im_read.py` — 能力内部方法
- `test_capabilities_contract.py` — 内部加载逻辑

这些测试覆盖的行为，若重要则应在 flow 测试中通过端到端场景覆盖；若不重要则直接删除。
