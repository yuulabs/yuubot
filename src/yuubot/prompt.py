"""Prompt management — Character × AgentSpec → PromptSpec.

Core data structures for building agent system prompts in a transparent,
inspectable way. All prompt assembly goes through build_prompt_spec() →
build_system_prompt(), eliminating scattered ad-hoc construction.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import attrs
from loguru import logger


@attrs.define
class FileRef:
    """Lazy file content loader — re-reads on every resolve() for hot-reload."""

    path: str

    def resolve(self) -> str:
        p = Path(self.path)
        if not p.is_absolute():
            # Resolve relative to the yuubot package root (src/yuubot/)
            pkg_root = Path(__file__).parent
            p = pkg_root / self.path
        return p.read_text(encoding="utf-8")


@attrs.define
class Section:
    """A named content block in the system prompt."""

    name: str
    content: str | FileRef | Callable[[], str]

    def resolve(self) -> str:
        if isinstance(self.content, FileRef):
            return self.content.resolve()
        if callable(self.content):
            return cast(Callable[[], str], self.content)()
        return self.content


@attrs.define
class AgentSpec:
    """Capability specification — tools + sections + constraints."""

    tools: list[str] = attrs.Factory(list)
    sections: list[Section] = attrs.Factory(list)
    # Caps: built-in capabilities (im, mem, web, etc.) — in-process execution
    caps: list[str] = attrs.Factory(list)
    expand_caps: list[str] = attrs.Factory(list)
    cap_visibility: dict[str, "CapVisibility"] = attrs.Factory(dict)
    cap_actions: dict[str, list[str]] = attrs.Factory(dict)
    # External skills: third-party capabilities — subprocess execution
    ext_skills: list[str] = attrs.Factory(list)
    expand_ext_skills: list[str] = attrs.Factory(list)
    subagents: list[str] = attrs.Factory(list)  # allowed delegate targets
    max_steps: int = 16
    soft_timeout: float | None = None
    silence_timeout: float | None = None
    tool_batch_timeout: float = 120.0


@attrs.define
class RuntimeInfo:
    """Provider/model info derived from config at dispatch time."""

    provider: str
    model: str
    supports_vision: bool = False


@attrs.define(frozen=True)
class CapVisibility:
    """Action visibility for one capability."""

    mode: str = "all"  # "all" | "include" | "exclude"
    actions: tuple[str, ...] = ()


@attrs.define
class Character:
    """A persona with a single AgentSpec."""

    name: str
    description: str
    min_role: str
    persona: str | FileRef
    spec: AgentSpec
    max_tokens: int = 60000  # compression threshold in input tokens
    llm_ref: str = ""  # runtime-mutable, canonical provider/model ref
    provider: str = ""  # runtime-mutable, populated from YAML at startup
    model: str = ""     # runtime-mutable, populated from YAML at startup
    render_policy: object | None = None  # RenderPolicy from daemon.render, optional

    def resolve_persona(self) -> str:
        if isinstance(self.persona, FileRef):
            return self.persona.resolve()
        return self.persona


@attrs.define
class PromptSpec:
    """Fully resolved, inspectable prompt structure."""

    character_name: str
    agent_spec: AgentSpec
    runtime: RuntimeInfo
    resolved_sections: list[tuple[str, str]]  # [(name, content)]
    tools: list[str]


_CONTROL_TOOLS = frozenset({
    "sleep",
    "inspect_background",
    "cancel_background",
    "input_background",
    "defer_background",
    "wait_background",
})


def _render_control_tools_section(tools: list[str]) -> str:
    present = _CONTROL_TOOLS & set(tools)
    if not present:
        return ""

    lines = [
        "<control_tools>",
        "你拥有一组用于管理长时间运行任务的控制工具。",
        "当工具因为 defer 被移到后台后，不要盲目等待；先检查，再决定是继续等待、发送输入、催促 delegate，还是取消。",
        "工作流建议：",
        "1. 需要了解后台进展时，调用 inspect_background。优先检查，而不是猜测。",
        "2. 需要最终结果时，调用 wait_background 等待一个或多个后台 run 完成。",
        "3. 交互式 bash 在后台等待输入时，调用 input_background 发送输入；这相当于写 stdin。",
        "4. 后台 run 是 delegate 时，input_background 相当于给该 agent 发送一条新消息。",
        "5. 后台 delegate 长时间卡住时，先 inspect_background；必要时再用 defer_background 催它先停止前台等待并汇报进展。",
        "6. sleep 和 wait_background 本身也是长工具，外部仍可能再次 defer 它们；这不是错误。",
        "</control_tools>",
    ]
    if "delegate" in tools:
        lines.insert(
            -1,
            "7. 如果你把 delegate defer 到后台，后续应把它当作普通后台 run 管理：inspect / input / defer / wait 都可以配合使用。",
        )
    return "\n".join(lines)


def _render_caps_summary(
    cap_names: list[str],
    cap_visibility: dict[str, CapVisibility],
) -> str:
    """Render compact capability summary for on-demand capabilities."""
    if not cap_names:
        return ""
    from yuubot.capabilities import capability_summary

    lines = ["<caps>"]
    lines.append(
        "Capability 是你的内置能力，每个 capability 提供一组子命令。"
        "你通过 call_cap_cli('cap_name subcommand --flags ...') 调用它们。"
    )
    lines.append(
        "⚠️ 重要：每个 capability 的参数格式各不相同，你无法猜到正确的参数。"
        "首次使用某个 capability 前，必须先调用 read_cap_doc('<name>') 阅读文档。"
        "传错参数会直接报错。"
    )
    lines.append("")
    lines.append("可用 capability 列表：")
    for name in cap_names:
        visibility = cap_visibility.get(name)
        desc = capability_summary(name)
        if desc:
            lines.append(f"<{name}>{desc}</{name}>")
        else:
            lines.append(f"<{name}>(no description)</{name}>")
        if visibility and visibility.mode != "all":
            label = "allowed_actions" if visibility.mode == "include" else "blocked_actions"
            lines.append(f"<{name}_{label}>{', '.join(visibility.actions)}</{name}_{label}>")
    lines.append("</caps>")
    return "\n".join(lines)


def _load_cap_docs(
    caps: list[str],
    expand_caps: list[str],
    cap_visibility: dict[str, CapVisibility],
) -> list[tuple[str, str]]:
    """Load capability docs and return (name, content) section pairs.

    Expanded capabilities get full doc inlined. Others get a summary.
    """
    from yuubot.capabilities import load_capability_doc, registered_capabilities
    from yuubot.capabilities.contract import ActionFilter

    available = set(registered_capabilities())
    expand_names = set(expand_caps)

    if "*" in caps:
        cap_list = sorted(available)
    else:
        cap_list = [a for a in caps if a in available]

    expanded = []
    remaining = []
    for name in cap_list:
        if name in expand_names:
            expanded.append(name)
        else:
            remaining.append(name)

    result: list[tuple[str, str]] = []

    # Summary for on-demand capabilities
    if remaining:
        summary = _render_caps_summary(remaining, cap_visibility)
        if summary:
            result.append(("caps_summary", summary))

    # Full doc for expanded capabilities
    for name in expanded:
        try:
            visibility = cap_visibility.get(name)
            af = ActionFilter(mode=visibility.mode, actions=frozenset(visibility.actions)) if visibility else None
            content = load_capability_doc(name, action_filter=af)
            result.append((
                f"expanded_cap:{name}",
                f'<cap_doc name="{name}">\n{content}\n</cap_doc>',
            ))
        except FileNotFoundError:
            logger.warning("No documentation for capability {}", name)
            remaining_fallback = _render_caps_summary([name], cap_visibility)
            if remaining_fallback:
                result.append(("caps_summary", remaining_fallback))

    return result


def _render_skills_summary(skills) -> str:
    """Render compact skills summary without exposing file paths."""
    if not skills:
        return ""
    lines = ["<skills>"]
    for s in skills:
        lines.append(f"<{s.name}>{s.description}</{s.name}>")
    lines.append("</skills>")
    lines.append(
        "\n使用 execute_skill_cli 工具执行上述 Skill 提供的 CLI 命令。\n"
        "⚠️ 首次调用某个 Skill 的命令前，必须先用 read_skill('<name>') 阅读其文档，"
        "确认参数格式后再调用。不要猜测参数。"
    )
    return "\n".join(lines)


def _load_skills_docs(
    skill_paths: list[str],
    ext_skills: list[str],
    expand_ext_skills: list[str],
) -> list[tuple[str, str]]:
    """Load skill docs and return (name, content) section pairs.

    Returns up to two sections: skills_summary and expanded skill docs.
    """
    try:
        from yuuagents.skills import scan  # type: ignore[unresolved-import]
    except ImportError:
        return []

    all_skills = scan(skill_paths)
    if not all_skills:
        return []

    # Filter to agent's allowed skills
    expand_names = set(expand_ext_skills)
    if "*" not in ext_skills:
        allowed = set(ext_skills)
        all_skills = [s for s in all_skills if s.name in allowed]

    expanded = []
    remaining = []
    for s in all_skills:
        if s.name in expand_names:
            expanded.append(s)
        else:
            remaining.append(s)

    result: list[tuple[str, str]] = []

    # Summary for non-expanded skills
    summary = _render_skills_summary(remaining)
    if summary:
        result.append(("skills_summary", summary))

    # Full SKILL.md for expanded skills
    for s in expanded:
        try:
            content = Path(s.location).read_text(encoding="utf-8")
            result.append((
                f"expanded:{s.name}",
                f'<skill_doc name="{s.name}">\n{content}\n</skill_doc>',
            ))
        except Exception:
            logger.warning("Failed to read SKILL.md for {} at {}", s.name, s.location)
            fallback = _render_skills_summary([s])
            if fallback:
                result.append(("skills_summary", fallback))

    return result


def build_prompt_spec(
    char: Character,
    runtime: RuntimeInfo,
    skill_paths: list[str] | None = None,
) -> PromptSpec:
    """Character × Runtime → PromptSpec. Deterministic derivation."""
    spec = char.spec
    cap_visibility = resolve_cap_visibility(spec)

    sections: list[tuple[str, str]] = []

    # [1] persona — always first
    persona_text = char.resolve_persona()
    sections.append(("persona", persona_text))

    # [2..N] agent spec sections
    for s in spec.sections:
        content = s.resolve()
        if content:
            sections.append((s.name, content))

    # [N+1..] capabilities
    if spec.caps or spec.expand_caps:
        cap_sections = _load_cap_docs(spec.caps, spec.expand_caps, cap_visibility)
        sections.extend(cap_sections)

    # [N+M..] external skills (third-party)
    if skill_paths and (spec.ext_skills or spec.expand_ext_skills):
        skill_sections = _load_skills_docs(
            skill_paths,
            spec.ext_skills,
            spec.expand_ext_skills,
        )
        sections.extend(skill_sections)

    # Resolve tool list (add view_image if vision-capable)
    tools = list(spec.tools)
    if runtime.supports_vision and "view_image" not in tools:
        tools.append("view_image")

    control_section = _render_control_tools_section(tools)
    if control_section:
        sections.append(("control_tools", control_section))

    return PromptSpec(
        character_name=char.name,
        agent_spec=spec,
        runtime=runtime,
        resolved_sections=sections,
        tools=tools,
    )


def resolve_cap_visibility(spec: AgentSpec) -> dict[str, CapVisibility]:
    """Normalize capability action visibility, keeping legacy cap_actions working."""
    result = dict(getattr(spec, "cap_visibility", {}))
    for name, actions in getattr(spec, "cap_actions", {}).items():
        result.setdefault(
            name,
            CapVisibility(mode="include", actions=tuple(actions)),
        )
    return result


def build_system_prompt(spec: PromptSpec) -> str:
    """PromptSpec → plain system prompt string."""
    return "\n\n".join(content for _name, content in spec.resolved_sections)


def format_prompt_spec(spec: PromptSpec) -> str:
    """Human-readable summary of a PromptSpec (for /yshow prompt)."""
    lines = [
        f"Character: {spec.character_name}",
        f"Provider: {spec.runtime.provider}/{spec.runtime.model}",
        f"Vision: {spec.runtime.supports_vision}",
        "",
        "System Prompt Sections:",
    ]
    for i, (name, content) in enumerate(spec.resolved_sections, 1):
        size = len(content)
        if size < 1024:
            lines.append(f"  [{i}] {name:<24} ({size} chars)")
        else:
            lines.append(f"  [{i}] {name:<24} ({size / 1024:.1f}k chars)")

    lines.append("")
    lines.append(f"Tools: {', '.join(spec.tools)}")

    agent = spec.agent_spec
    lines.append(f"Max steps: {agent.max_steps}")
    if agent.soft_timeout:
        lines.append(f"Soft timeout: {agent.soft_timeout}s")
    if agent.silence_timeout:
        lines.append(f"Silence timeout: {agent.silence_timeout}s")

    return "\n".join(lines)
