"""Character registry — single source of truth for agent behavior.

Each submodule defines one Character and calls register() at import time.
"""

from __future__ import annotations

from yuubot.prompt import AgentSpec, Character, FileRef, Section


# ── Registry ─────────────────────────────────────────────────────

CHARACTER_REGISTRY: dict[str, Character] = {}


def get_character(name: str) -> Character:
    """Look up a character by name. Raises KeyError if not found."""
    return CHARACTER_REGISTRY[name]


def register(char: Character) -> None:
    """Add or replace a character in the registry."""
    CHARACTER_REGISTRY[char.name] = char


def unregister(name: str) -> None:
    """Remove a character from the registry. No-op if not found."""
    CHARACTER_REGISTRY.pop(name, None)


# ── Shared sections ──────────────────────────────────────────────

SLEEP_MECHANISM = Section(
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


def docker_section() -> Section:
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


def subagents_section(*agent_names: str) -> Section:
    """Build a section listing available delegate targets."""
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


def bootstrap_section(path: str) -> Section:
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


# ── Import all character submodules to trigger registration ──────

from yuubot.characters import main      # noqa: E402, F401
from yuubot.characters import general   # noqa: E402, F401
from yuubot.characters import researcher  # noqa: E402, F401
from yuubot.characters import coder     # noqa: E402, F401
from yuubot.characters import curator   # noqa: E402, F401
