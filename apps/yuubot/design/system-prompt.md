# System Prompt 契约

> 本文档定义 yuubot Conversation / IM 模式下 Agent System Prompt 的**五段式契约**。
> 它是 prompt 贡献者扩展系统提示时的强制结构约束——任何修改 prompt 渲染的代码都必须遵守此顺序。

---

## 1. 五个固定段落

`_render_system_prompt(binding, mode)` 严格按以下顺序拼接，以空行分隔：

```text
# Character
<Character.system_prompt 原文>

# System Instructions
<工作面散文 + 工作区约定 + （仅 im 模式）IM 用户可见性语义>

# Integration Prompt Sections
<GitHub facade 指南（可见时）；为空时输出 No integration capabilities configured.>

# AGENTS.md Context
<工作区根目录下 AGENTS.md 全文 + 冻结说明；缺省时输出缺省行>

# Real-Time Data
<平台 / 绝对 ISO 时间 / 时区>
```

段落头部使用稳定的英文 marker，便于测试按顺序匹配。每个段落头部都至少出现一次；
`Integration Prompt Sections` 即使无任何 integration capability 也保留头部，
其余段落（当前仅 Extension Zone）若内容为空则**不**渲染为可见头部。

### 1.1 Character（第 1 段）

`binding.character.system_prompt.strip()` 原文。Character 由历史末尾上移到首位，
因此断言从 `endswith("Base prompt.")` 迁移到 `startswith("# Character\nBase prompt.")`。

### 1.2 System Instructions（第 2 段）

手工撰写的工具工作面散文，包含 `bash` / `read` / `edit` / `write` / `execute_python`
四类工具的使用边界。`execute_python` 仅在本段以“integration-call surface”身份出现，
其 ipykernel cell 语义、崩溃/重置策略由 `_python_tool.py` 的 tool spec 拥有，
本段**不**复制这些细节。

仅当 `binding.workspace_path` 非空时追加工作区约定 bullet（绝对路径 / cwd / 子目录 /
`tmp/`、`artifacts/` / `AGENTS.md` 项目地图）。

Figure-delivery 契约（savefig → workspace browser URL → 对话视图内联渲染）的完整语法、管线与约束见 `artifact-delivery.md`。

仅当 `mode == "im"` 时追加 `IM_MODE_SYSTEM_GUIDANCE`（系统级用户可见性语义：
incoming mailbox 消息只是输入；用户可见回复走 `tim.Channel(path).send(text)`；
普通 assistant 文本不投递给 IM 用户）。该块是第 2 段的一部分，**不**作为独立的
Extension Section 段落渲染。

### 1.3 Integration Prompt Sections（第 3 段）

由 capability-guidance 渲染器输出。`bash` / `read` / `edit` / `write` /
`execute_python` 是第 2 段手工散文的内容，本段不重复。

`github.*` capability 可见时输出 hand-written GitHub facade 段（包含 `yext.github`
import、`await` 示例、每个 capability 的 read/write effect 标签、failure 指引）。
该段**不**包含机械的 id-to-module 映射文本（如
`Map a capability id to yext by keeping the prefix ...`、
`Example: github.issue.list -> await yext.github.issue.list(...)`、
`Non-builtin capabilities are async Python facade functions exposed through execute_python.`）。

无任何 integration capability 可见时输出 `No integration capabilities configured.`。

非 GitHub 命名空间的 capability 走 generic fallback 子段（按 capability id 列出，
禁止机械 id-to-module 推导）。当前阶段无此类 capability，fallback 仅作为结构性占位。

### 1.4 AGENTS.md Context（第 4 段）

当 `{workspace_path}/AGENTS.md` 存在时，输出全文并追加冻结说明：

> Editing AGENTS.md only affects future agent instantiations. The current
> conversation will keep using the snapshot assembled at first send.

不存在时输出 `No AGENTS.md found at the workspace root.`。

### 1.5 Real-Time Data（第 5 段）

- platform：`platform.system().lower()`。
- 绝对 ISO 日期时间：`datetime.now().strftime("%Y-%m-%d %H:%M:%S")`。
- 时区：会话级用户上下文（请求/用户）可用时从该上下文派生；否则回退到服务器本地时区。
  Conversation 模式期望用户上下文可用；IM Actor 运行无用户上下文时回退到服务器时间。

---

## 2. 冻结时机

System prompt 在同一次 runtime 生命周期内**仅渲染一次**：第一次 `send_message` 触发
`ensure_conversation_agent` → `build_agent_definition` → `_system_prompt` 渲染，结果
作为 `system` message 加入 agent.history 前缀。后续 turn 复用同一 agent（
由 `conversation_agents[conversation_id]` 缓存），磁盘/时钟变化不影响后续 LLM 调用
看到的 system message。

跨 daemon 重启的 freeze（持久化 model history，新 runtime 从持久化的 system
message 重建 agent，而非重新渲染）由 Phase 5.4 拥有，本阶段不实现。

---

## 3. Extension Zone —— 代码约定（不渲染）

当未来某次变更需要为 system prompt 贡献**不属于**上述五段中任何一段的内容时，扩展点位于
渲染器代码路径中 `# AGENTS.md Context` **之前**：

```python
sections = [
    _render_character(binding),
    _render_system_instructions(binding, mode),
    _render_integration_sections(binding),
    _render_extension_fragments(),   # ← 今日返回 ""；扩展插入点
    _render_agents_md_context(binding.workspace_path),
    _render_realtime(),
]
```

`_render_extension_fragments()` 今日恒返回空串，组装结果**不**包含任何
`# Extension Section` 头部。该约定对 agent 不可见。
