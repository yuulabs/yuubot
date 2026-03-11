"""Prompt management — Character × AgentSpec → PromptSpec.

Core data structures for building agent system prompts in a transparent,
inspectable way. All prompt assembly goes through build_prompt_spec() →
build_system_prompt(), eliminating scattered ad-hoc construction.
"""

from __future__ import annotations

from pathlib import Path

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
            return self.content()
        return self.content


@attrs.define
class AgentSpec:
    """Capability specification — tools + sections + constraints."""

    tools: list[str] = attrs.Factory(list)
    sections: list[Section] = attrs.Factory(list)
    # Addons: built-in capabilities (im, mem, web, etc.) — in-process execution
    addons: list[str] = attrs.Factory(list)
    expand_addons: list[str] = attrs.Factory(list)
    # Skills: third-party capabilities — subprocess execution
    skills: list[str] = attrs.Factory(list)
    expand_skills: list[str] = attrs.Factory(list)
    subagents: list[str] = attrs.Factory(list)  # allowed delegate targets
    max_steps: int = 16
    soft_timeout: float | None = None
    silence_timeout: float | None = None


@attrs.define
class RuntimeInfo:
    """Provider/model info derived from config at dispatch time."""

    provider: str
    model: str
    supports_vision: bool = False


@attrs.define
class Character:
    """A persona with a single AgentSpec."""

    name: str
    description: str
    min_role: str
    persona: str | FileRef
    spec: AgentSpec
    max_tokens: int = 60000  # compression threshold in input tokens
    provider: str = ""  # runtime-mutable, populated from YAML at startup
    model: str = ""     # runtime-mutable, populated from YAML at startup

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


def _render_addons_summary(addon_names: list[str]) -> str:
    """Render compact addon summary for on-demand addons."""
    if not addon_names:
        return ""
    from yuubot.addons import addon_summary

    lines = ["<addons>"]
    for name in addon_names:
        desc = addon_summary(name)
        if desc:
            lines.append(f"<{name}>{desc}</{name}>")
        else:
            lines.append(f"<{name}>(no description)</{name}>")
    lines.append("</addons>")
    lines.append(
        "\n使用 execute_addon_cli 工具执行上述 Addon 命令。\n"
        "⚠️ 首次调用某个 Addon 的命令前，必须先用 read_addon_doc('<name>') 阅读其文档，"
        "确认参数格式后再调用。不要猜测参数。"
    )
    return "\n".join(lines)


def _load_addon_docs(
    addons: list[str],
    expand_addons: list[str],
) -> list[tuple[str, str]]:
    """Load addon docs and return (name, content) section pairs.

    Expanded addons get full doc inlined. Others get a summary.
    """
    from yuubot.addons import load_addon_doc, registered_addons

    available = set(registered_addons())
    expand_names = set(expand_addons)

    # Resolve addon list
    if "*" in addons:
        addon_list = sorted(available)
    else:
        addon_list = [a for a in addons if a in available]

    expanded = []
    remaining = []
    for name in addon_list:
        if name in expand_names:
            expanded.append(name)
        else:
            remaining.append(name)

    result: list[tuple[str, str]] = []

    # Summary for on-demand addons
    if remaining:
        summary = _render_addons_summary(remaining)
        if summary:
            result.append(("addons_summary", summary))

    # Full doc for expanded addons
    for name in expanded:
        try:
            content = load_addon_doc(name)
            result.append((
                f"expanded_addon:{name}",
                f'<addon_doc name="{name}">\n{content}\n</addon_doc>',
            ))
        except FileNotFoundError:
            logger.warning("No documentation for addon {}", name)
            remaining_fallback = _render_addons_summary([name])
            if remaining_fallback:
                result.append(("addons_summary", remaining_fallback))

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
    skills: list[str],
    expand_skills: list[str],
) -> list[tuple[str, str]]:
    """Load skill docs and return (name, content) section pairs.

    Returns up to two sections: skills_summary and expanded skill docs.
    """
    try:
        from yuuagents.skills import scan
    except ImportError:
        return []

    all_skills = scan(skill_paths)
    if not all_skills:
        return []

    # Filter to agent's allowed skills
    expand_names = set(expand_skills)
    if "*" not in skills:
        allowed = set(skills)
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

    sections: list[tuple[str, str]] = []

    # [1] persona — always first
    persona_text = char.resolve_persona()
    sections.append(("persona", persona_text))

    # [2..N] agent spec sections
    for s in spec.sections:
        content = s.resolve()
        if content:
            sections.append((s.name, content))

    # [N+1..] addons
    if spec.addons or spec.expand_addons:
        addon_sections = _load_addon_docs(spec.addons, spec.expand_addons)
        sections.extend(addon_sections)

    # [N+M..] skills (third-party)
    if skill_paths and (spec.skills or spec.expand_skills):
        skill_sections = _load_skills_docs(skill_paths, spec.skills, spec.expand_skills)
        sections.extend(skill_sections)

    # Resolve tool list (add view_image if vision-capable)
    tools = list(spec.tools)
    if runtime.supports_vision and "view_image" not in tools:
        tools.append("view_image")

    return PromptSpec(
        character_name=char.name,
        agent_spec=spec,
        runtime=runtime,
        resolved_sections=sections,
        tools=tools,
    )


def build_system_prompt(spec: PromptSpec):
    """PromptSpec → yuuagents SimplePromptBuilder."""
    from yuuagents.agent import SimplePromptBuilder

    builder = SimplePromptBuilder()
    for _name, content in spec.resolved_sections:
        builder.add_section(content)
    return builder


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
