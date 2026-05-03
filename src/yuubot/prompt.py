"""RFC2 character and prompt definitions for yuubot.

System prompts are assembled from an explicit ``prompt_sections`` tuple on
``AgentSpec``.  Every piece of the final prompt is declared at definition time;
``render_system_prompt`` is a mechanical renderer with no hidden insertions.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Union

import attrs
import yuuagents as ya
from yuuagents.python_runtime import ResolvedPythonRuntime

from yuubot.daemon.restricted_python import _ALLOWED_IMPORTS as _RESTRICTED_ALLOWED_IMPORTS


# ── Prompt section types ──────────────────────────────────────────────────────

@attrs.define(frozen=True)
class FileSection:
    """Load a .md file from the yuubot/prompts/ directory."""

    path: str  # relative to prompts/, e.g. "main/persona.md"


@attrs.define(frozen=True)
class InlineSection:
    """Literal text embedded directly in the spec definition."""

    content: str


@attrs.define(frozen=True)
class DelegatesSection:
    """Descriptions of delegatable agents, resolved from delegate_policy at render time."""


@attrs.define(frozen=True)
class PythonWorkerSection:
    """Python execution backend description.

    Resolved at build_definition() time via the python_backend parameter:
    - "kernel"     → long-lived PythonSession, top-level await, cross-turn state
    - "restricted" → sandboxed per-turn worker, sync-only, no file/net/process access
    - ""           → rendered as nothing (e.g. when previewing without a bot_kind)
    """


@attrs.define(frozen=True)
class ExpandFunctionsSection:
    """Resolved import docs injected as a system-prompt section.

    Rendered from the ResolvedPythonRuntime passed to render_system_prompt().
    Replaces the legacy tool-description injection so function docs are visible
    as plain text in traces rather than buried inside a JSON tool spec.
    """


PromptSection = Union[FileSection, InlineSection, DelegatesSection, PythonWorkerSection, ExpandFunctionsSection]

# Shared: Python session runtime instructions for any execute_python agent.
PYTHON_RUNTIME_SECTION = InlineSection(
    content=(
        "你可以通过 execute_python 使用持久 Python session 处理需要工具的任务。每一段code类似于jupyter cell，将会持续活跃，直至session被关闭。\n"
        "业务函数已作为可 import 模块注入（见下方函数文档）；使用前必须先 `import 模块名`，例如 `import yb`，再调用 `yb.*`。\n"
        "如需安装或更新 Python 依赖，默认使用 uv 管理包，例如通过 `yb.bash(\"uv add 包名\")` 或 `yb.bash(\"uv sync\")`；不要默认使用 pip。\n"
        "\n"
        "SESSION_STATE 是一个 msgspec.Struct 对象，通过属性访问（如 SESSION_STATE.ctx_id），"
        "不可 json.dumps；包含字段：bot_kind、ctx_id、chat_type、group_id、user_id、"
        "conversation_id、agent_name、character_name、agent_id、task_id、bot_id、bot_name、"
        "workspace_root、recorder_base_url、daemon_base_url、delegate_depth、"
        "token、python_backend、supports_vision。\n"
        "TASKS 是当前 Python session 中用于长期 asyncio task 的字典，你可用它来保存长期任务\n"
        "；session 关闭后不会保留。"
    )
)


# ── DelegatePolicy ────────────────────────────────────────────────────────────

@attrs.define(frozen=True)
class DelegatePolicy:
    """Static delegate-policy skeleton rendered into prompts and checked later."""

    allowed_agents: tuple[str, ...] = ()
    max_depth: int = 0
    max_concurrency: int = 0
    timeout_s: float | None = None


# ── AgentSpec ─────────────────────────────────────────────────────────────────

@attrs.define(frozen=True)
class AgentSpec:
    """RFC2 runtime metadata for one yuubot character."""

    tools: tuple[str, ...]
    prompt_sections: tuple[PromptSection, ...] = ()
    import_modules: tuple[ya.PythonImport | str, ...] = ()
    expand_functions: tuple[str, ...] = ("*",)
    startup_code: str = ""
    delegate_policy: DelegatePolicy | None = None
    max_turns: int | None = 1
    max_context_tokens: int | None = None
    idle_agent_ttl_s: int = 300
    preserve_python_session: bool = True
    facade_module: str = ""  # kept for backward compat; prefer import_modules

    def resolved_imports(self) -> tuple[ya.PythonImport, ...]:
        if self.import_modules:
            return tuple(
                item if isinstance(item, ya.PythonImport) else ya.PythonImport(str(item))
                for item in self.import_modules
            )
        if self.facade_module:
            return (ya.PythonImport(self.facade_module, alias="yb"),)
        return ()


# ── Character ─────────────────────────────────────────────────────────────────

@attrs.define(frozen=True)
class Character:
    """A yuubot character that derives yuuagents definition/runtime skeletons."""

    name: str
    description: str
    spec: AgentSpec
    bot_kind: Literal["master", "group"]

    def supports_bot_kind(self, bot_kind: str) -> bool:
        return self.bot_kind == bot_kind


# ── Rendering ─────────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_file(path: str) -> str:
    return (_PROMPTS_DIR / path).read_text(encoding="utf-8").strip()


_KERNEL_WORKER_TEXT = (
    "当前是 master 私聊：execute_python 使用完整、长生命周期 Python kernel。"
    "可以使用 top-level await，变量和 TASKS 会跨 agent 会话尽量保留。例如，当你可能执行一个长时间任务时，请使用 `TASK['x'] = asyncio.create_task(...)`, 然后使用asyncio.wait配合timeout等待结果，以避免长时间等待。"
)
_RESTRICTED_WORKER_TEXT = (
    "当前是群聊/非 master 场景：execute_python 使用受限 Python worker。"
    "业务模块（im、mem、web、vision 等）已预注入命名空间，无需 import，直接 `await im.send_message(...)` 即可。"
    "支持 top-level await，异步函数需用 await 调用。"
    f"可 import 的标准库白名单：{', '.join(sorted(_RESTRICTED_ALLOWED_IMPORTS))}。"
    "不要执行文件、网络、进程、反射或 import 白名单外的模块；while 循环不可用。"
    "如果 worker 超时或崩溃，本次工具调用会失败并可能重启 worker。"
)


def render_system_prompt(
    character: Character,
    *,
    delegate_descriptions: Iterable[tuple[str, str]] = (),
    python_backend: str = "",
    python_runtime: ResolvedPythonRuntime | None = None,
) -> str:
    """Render the system prompt from prompt_sections; no hidden insertions."""
    delegate_list = list(delegate_descriptions)
    parts: list[str] = []

    for section in character.spec.prompt_sections:
        if isinstance(section, FileSection):
            parts.append(_load_file(section.path))
        elif isinstance(section, InlineSection):
            parts.append(section.content.strip())
        elif isinstance(section, DelegatesSection):
            if delegate_list:
                lines = ["当前 policy 引导下可考虑委派的 agent："]
                lines.extend(f"- {name}: {desc}" for name, desc in delegate_list)
                parts.append("\n".join(lines))
            else:
                parts.append("当前未向你暴露可委派 agent；不要自行猜测可委派目标。")
        elif isinstance(section, PythonWorkerSection):
            if python_backend == "kernel":
                parts.append(_KERNEL_WORKER_TEXT)
            elif python_backend == "restricted":
                parts.append(_RESTRICTED_WORKER_TEXT)
        elif isinstance(section, ExpandFunctionsSection):
            if python_runtime is not None:
                text = python_runtime.tool_description_suffix()
                if text:
                    parts.append(text)

    return "\n\n".join(parts)
