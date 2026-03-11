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


def _load_skills_docs(
    skill_paths: list[str],
    skills: list[str],
    expand_skills: list[str],
) -> list[tuple[str, str]]:
    """Load skill docs and return (name, content) section pairs.

    Returns up to two sections: skills_summary and expanded skill docs.
    """
    try:
        from yuuagents.skills import scan, render
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
    summary = render(remaining)
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
            logger.warning("Failed to read SKILL.md for %s at %s", s.name, s.location)
            # Fallback: add to remaining for summary
            fallback = render([s])
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

    # [N+1..] skills
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
