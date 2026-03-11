"""Character inspection and runtime config mutation: /ychar."""


from yuubot.core.models import Role

from loguru import logger


async def exec_char_show_prompt(remaining: str, event: dict, deps: dict) -> str | None:
    """Show system prompt sections with sizes for a character."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character
    from yuubot.prompt import build_prompt_spec, RuntimeInfo

    name = remaining.strip() or "main"
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    char = get_character(name)

    # Build a minimal RuntimeInfo for display
    runtime = RuntimeInfo(
        provider=char.provider or "?",
        model=char.model or "?",
    )
    spec = build_prompt_spec(char, runtime)

    from yuubot.prompt import format_prompt_spec
    return format_prompt_spec(spec)


async def exec_char_show_config(remaining: str, event: dict, deps: dict) -> str | None:
    """Show character config (provider, model, tools, etc.)."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character

    name = remaining.strip() or "main"
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    char = get_character(name)
    spec = char.spec

    lines = [
        f"Character: {char.name}",
        f"Description: {char.description}",
        f"Min role: {char.min_role}",
        f"Provider: {char.provider or '(from YAML)'}",
        f"Model: {char.model or '(from YAML)'}",
        "",
        f"Tools: {', '.join(spec.tools) or '(none)'}",
        f"Skills: {', '.join(spec.skills) or '(none)'}",
        f"Subagents: {', '.join(spec.subagents) or '(none)'}",
        f"Max steps: {spec.max_steps}",
    ]
    if spec.soft_timeout:
        lines.append(f"Soft timeout: {spec.soft_timeout}s")
    if spec.silence_timeout:
        lines.append(f"Silence timeout: {spec.silence_timeout}s")

    return "\n".join(lines)


async def exec_char_config(remaining: str, event: dict, deps: dict) -> str | None:
    """Hot-swap provider/model at runtime: /ychar config <name> key=value ..."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character

    parts = remaining.strip().split()
    if not parts:
        return "用法: /char config <name> provider=x model=y"

    name = parts[0]
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    char = get_character(name)
    changes = []
    for kv in parts[1:]:
        if "=" not in kv:
            continue
        key, value = kv.split("=", 1)
        if key == "provider":
            char.provider = value
            changes.append(f"provider → {value}")
        elif key == "model":
            char.model = value
            changes.append(f"model → {value}")
        else:
            return f"未知配置项: {key}. 支持: provider, model"

    if not changes:
        return "用法: /char config <name> provider=x model=y"

    return f"已更新 {name}: {', '.join(changes)}"


async def exec_char_list(remaining: str, event: dict, deps: dict) -> str | None:
    """List all registered characters."""
    from yuubot.characters import CHARACTER_REGISTRY

    lines = []
    for name, char in CHARACTER_REGISTRY.items():
        lines.append(f"- {name}: {char.description} (min_role={char.min_role})")
    return "\n".join(lines) or "(空)"
