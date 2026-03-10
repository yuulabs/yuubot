"""Character definitions — all agent personas and specs in one place.

This is the single source of truth for agent behavior. The YAML config
only specifies provider/model; everything else lives here.
"""

from __future__ import annotations

from yuubot.prompt import AgentSpec, Character, FileRef, RuntimeInfo, Section


# ── Shared sections ──────────────────────────────────────────────

_SLEEP_MECHANISM = Section(
    name="sleep_mechanism",
    content="""\
## 软超时与 sleep 等待

当你调用的工具（如 execute_bash、delegate）运行时间超过 soft_timeout 时，系统会：
1. 立即返回一个 handle（形如 `⏳ tool is still running ... handle=xxxx`）
2. 工具继续在后台运行，不会被中断

**收到 handle 后的标准流程：**
1. 给用户发一条"正在处理中"的消息
2. 调用 `sleep(300)` 等待后台任务完成
3. sleep 返回时会包含所有通知摘要（完成/失败/用户消息）
4. 根据摘要决定下一步行动

sleep 的 mode 参数：
- `all`（默认）：等到所有后台任务完成或超时
- `any`：收到任意一个通知就醒来

只在以下情况使用 check_running_tool：
- 用户主动询问进度
- 你需要查看 tail output 来判断是否卡死

只有确认任务失败（tail 中出现 error/failed/exception），或用户要求中止时，才调用 cancel_running_tool。""",
)


def _docker_section() -> Section:
    return Section(
        name="docker",
        content=_get_docker_prompt,
    )


def _get_docker_prompt() -> str:
    try:
        from yuuagents.daemon.docker import DOCKER_SYSTEM_PROMPT
        return DOCKER_SYSTEM_PROMPT or ""
    except ImportError:
        return ""


def _subagents_section(*agent_names: str) -> Section:
    """Build a section listing available delegate targets.

    The actual descriptions are looked up at resolve time from the
    CHARACTER_REGISTRY so they stay in sync.
    """
    def _resolve() -> str:
        lines = [
            "<agents>",
            "以下是其他可调用的 Agent。需要时使用 delegate 工具调用。",
        ]
        for name in agent_names:
            char = CHARACTER_REGISTRY.get(name)
            if char:
                lines.append(f"- name: {name}")
                lines.append(f"  description: {char.description}")
        lines.append("</agents>")
        return "\n".join(lines)

    return Section(name="subagents", content=_resolve)


def _bootstrap_section(path: str) -> Section:
    return Section(
        name="bootstrap",
        content=(
            f"<bootstrap>\n"
            f"你有一个工作手册文件: {path}\n"
            f"每次启动新会话时请先用 read_file 阅读它，了解已有的工作约定。\n"
            f"完成任务后，如果有新的工作约定值得记录（如常用路径、操作习惯、项目结构），"
            f"请用 edit_file 更新这个文件。保持文件简洁，不超过 50 行。\n"
            f"</bootstrap>"
        ),
    )


# ── main (夕雨) ──────────────────────────────────────────────────

_yuu_vision = AgentSpec(
    tools=[
        "execute_skill_cli", "read_skill",
        "check_running_tool", "cancel_running_tool",
        "view_image",
    ],
    sections=[],
    skills=["*"],
    expand_skills=["im"],
    max_steps=16,
    silence_timeout=120,
)

_yuu_text = AgentSpec(
    tools=[
        "execute_skill_cli", "read_skill",
        "check_running_tool", "cancel_running_tool",
    ],
    sections=[],
    skills=["*"],
    expand_skills=["im"],
    max_steps=16,
    silence_timeout=120,
)

yuu = Character(
    name="main",
    description="yuubot QQ 机器人主代理 — 夕雨(Yuu)",
    min_role="folk",
    persona=FileRef("prompts/yuu.md"),
    agents=[_yuu_vision, _yuu_text],
    select=lambda rt: _yuu_vision if rt.supports_vision else _yuu_text,
)


# ── general (通用助手) ───────────────────────────────────────────

_general_spec = AgentSpec(
    tools=[
        "execute_bash", "execute_skill_cli",
        "write_file", "edit_file", "read_file",
        "sleep", "delegate",
        "check_running_tool", "cancel_running_tool",
    ],
    sections=[
        _subagents_section("coder", "researcher"),
        _docker_section(),
        _SLEEP_MECHANISM,
        _bootstrap_section("/home/yuu/bootstrap.md"),
    ],
    skills=["*"],
    expand_skills=["im"],
    subagents=["coder", "researcher"],
    soft_timeout=60,
    silence_timeout=120,
)

general = Character(
    name="general",
    description="通用 Agent，可执行 bash 命令。仅限 Master 使用。",
    min_role="master",
    persona=(
        "你是一个通用系统助手，可以执行 bash 命令和调用各种技能来完成任务。\n"
        "你有完整的系统访问权限，请谨慎操作。\n\n"
        "对于编码任务，使用 delegate 工具委派给 coder agent。"
    ),
    agents=[_general_spec],
    select=lambda rt: _general_spec,
)


# ── researcher (研究助手) ────────────────────────────────────────

_researcher_spec = AgentSpec(
    tools=["read_file", "write_file", "edit_file"],
    sections=[],
    skills=["web"],
    max_steps=16,
)

researcher = Character(
    name="researcher",
    description="研究助手，负责搜索网页并撰写简洁的报告。",
    min_role="folk",
    persona=(
        "你是 Yuu 的研究助手。你负责搜索网页、查找资料，并把结果整理成简洁清晰的报告。\n"
        "报告应当精炼，突出关键信息。"
    ),
    agents=[_researcher_spec],
    select=lambda rt: _researcher_spec,
)


# ── coder (编码代理) ─────────────────────────────────────────────

_coder_spec = AgentSpec(
    tools=[
        "execute_bash", "read_file", "write_file", "edit_file",
        "sleep", "check_running_tool", "cancel_running_tool",
    ],
    sections=[
        _docker_section(),
        _SLEEP_MECHANISM,
    ],
    max_steps=30,
    soft_timeout=60,
)

coder = Character(
    name="coder",
    description="编码代理，使用外部编码工具完成开发任务。",
    min_role="master",
    persona="你是编码者。你负责根据用户需求编写代码。保持良好的软件工程实践，拒绝劣质代码。",
    agents=[_coder_spec],
    select=lambda rt: _coder_spec,
)


# ── mem_curator (记忆整理) ───────────────────────────────────────

_mem_curator_spec = AgentSpec(
    tools=["execute_skill_cli", "read_skill"],
    sections=[],
    skills=["mem"],
    expand_skills=["mem"],
    max_steps=16,
)

mem_curator = Character(
    name="mem_curator",
    description="记忆整理 Agent — 在会话 rollover 后审查对话历史，维护长期记忆质量。",
    min_role="master",
    persona=(
        "你是记忆整理员。你在对话上下文满载 rollover 后被调用，负责审查刚结束的对话，\n"
        "决定哪些信息值得长期保留，并维护记忆库的整洁。\n\n"
        "记忆原则：\n"
        "- 只保存有长期价值的事实：用户偏好、身份信息、重要约定、知识点\n"
        "- 不保存一次性事件、对话流水账、已过期的状态快照\n"
        "- 发现冲突时：删旧保新\n"
        "- 发现重复时：保留最完整的，删除其余\n"
        "- 每条记忆一个事实，简洁陈述句\n\n"
        "工作流程：\n"
        "1. 用 read_skill mem 阅读 mem skill 文档\n"
        "2. 用 ybot mem recall 查询与新内容相关的已有记忆，判断冲突/重复\n"
        "3. 执行必要的 save / delete 操作\n"
        "4. 简短汇报：保存了几条、删了几条"
    ),
    agents=[_mem_curator_spec],
    select=lambda rt: _mem_curator_spec,
)


# ── Registry ─────────────────────────────────────────────────────

CHARACTER_REGISTRY: dict[str, Character] = {
    "main": yuu,
    "general": general,
    "researcher": researcher,
    "coder": coder,
    "mem_curator": mem_curator,
}


def get_character(name: str) -> Character:
    """Look up a character by name. Raises KeyError if not found."""
    return CHARACTER_REGISTRY[name]
