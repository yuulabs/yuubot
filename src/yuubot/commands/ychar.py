"""Character inspection and selector-based runtime config mutation: /ychar."""

from __future__ import annotations

from yuubot.commands.tree import CommandRequest
from yuubot.model_resolution import ModelResolver


def _resolver(request: CommandRequest) -> ModelResolver:
    agent_runner = request.deps.get("agent_runner")
    if agent_runner is not None:
        runtime = getattr(agent_runner, "runtime", None)
        resolver = getattr(runtime, "model_resolver", None)
        if resolver is not None:
            return resolver
    return ModelResolver(request.deps["config"])


def _current_agent_name(request: CommandRequest) -> str:
    session_mgr = request.deps.get("session_mgr")
    ctx_id = request.message.ctx_id
    if session_mgr is not None and ctx_id:
        current = session_mgr.current_agent(ctx_id)
        if current:
            return current
        conv = session_mgr.get(ctx_id)
        if conv is not None and getattr(conv, "agent_name", ""):
            return conv.agent_name
    return "main"


async def exec_char_show_prompt(request: CommandRequest) -> str | None:
    """Show system prompt sections with sizes for a character."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character
    from yuubot.prompt import build_prompt_spec, RuntimeInfo

    name = request.remaining.strip() or "main"
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    char = get_character(name)
    resolver = _resolver(request)
    runtime = await resolver.resolve_agent(name)
    spec = build_prompt_spec(
        char,
        RuntimeInfo(
            provider=runtime.resolved_provider,
            model=runtime.resolved_model,
            supports_vision=runtime.supports_vision,
        ),
    )

    from yuubot.prompt import format_prompt_spec

    return format_prompt_spec(spec)


async def exec_char_show_config(request: CommandRequest) -> str | None:
    """Show character config (selector ref, resolved provider/model, tools, etc.)."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character

    name = request.remaining.strip() or "main"
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    char = get_character(name)
    resolver = _resolver(request)
    resolved = await resolver.resolve_agent(name)
    ref = resolver.get_agent_llm_ref(name)
    spec = char.spec

    lines = [
        f"Character: {char.name}",
        f"Description: {char.description}",
        f"Min role: {char.min_role}",
        f"LLM ref: {ref}",
        f"Resolved: {resolved.resolved_provider}/{resolved.resolved_model}",
        f"Selector: {resolved.selector}",
        f"Family: {resolved.family or '(unknown)'}",
        f"Vision: {resolved.supports_vision}",
        "",
        f"Tools: {', '.join(spec.tools) or '(none)'}",
        f"Capabilities: {', '.join(spec.caps) or '(none)'}",
        f"External skills: {', '.join(spec.ext_skills) or '(none)'}",
        f"Subagents: {', '.join(spec.subagents) or '(none)'}",
        f"Max steps: {spec.max_steps}",
    ]
    if spec.soft_timeout:
        lines.append(f"Soft timeout: {spec.soft_timeout}s")
    if spec.silence_timeout:
        lines.append(f"Silence timeout: {spec.silence_timeout}s")

    return "\n".join(lines)


async def exec_char_config(request: CommandRequest) -> str | None:
    """Hot-swap selector-based model config: /ychar config <name> llm=provider/model"""
    from yuubot.characters import CHARACTER_REGISTRY, get_character

    parts = request.remaining.strip().split()
    if not parts:
        return "用法: /char config <name> llm=<provider>/<selector-or-model>"

    name = parts[0]
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    llm_ref = ""
    for kv in parts[1:]:
        if kv.startswith("llm="):
            llm_ref = kv.split("=", 1)[1].strip()
            break
    if not llm_ref:
        return "用法: /char config <name> llm=<provider>/<selector-or-model>"

    resolver = _resolver(request)
    try:
        resolved = await resolver.resolve_ref(llm_ref)
    except Exception as exc:
        return f"无法解析 llm={llm_ref}: {exc}"

    char = get_character(name)
    char.llm_ref = llm_ref
    config_refs = getattr(request.deps["config"], "agent_llm_refs", None)
    if isinstance(config_refs, dict):
        config_refs[name] = llm_ref
    return (
        f"已更新 {name}: llm={llm_ref} -> "
        f"{resolved.resolved_provider}/{resolved.resolved_model} "
        f"(family={resolved.family or 'unknown'}, vision={resolved.supports_vision})"
    )


async def exec_char_alias(request: CommandRequest) -> str | None:
    """Create or inspect selector bindings."""
    text = request.remaining.strip()
    if not text or " as " not in text:
        return (
            "用法: /char alias <provider>/<model> as <selector>\n"
            "     /char alias * as <selector>"
        )

    source_raw, selector = [part.strip() for part in text.split(" as ", 1)]
    if not selector:
        return "selector 不能为空"

    resolver = _resolver(request)
    if source_raw == "*":
        agent_name = _current_agent_name(request)
        resolved = await resolver.bind_current(agent_name, selector)
        return (
            f"已绑定 {selector}: "
            f"{resolved.resolved_provider}/{resolved.resolved_model} "
            f"(from {agent_name})"
        )

    resolved_source = await resolver.resolve_ref(source_raw)
    bound = resolver.bind_resolved(resolved_source, selector)
    return f"已绑定 {selector}: {bound.resolved_provider}/{bound.resolved_model}"


async def exec_char_alias_show(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    selector = request.remaining.strip()
    if not selector:
        return "用法: /char alias show <selector>"
    return resolver.show_selector(selector)


async def exec_char_alias_refresh(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    ref = request.remaining.strip()
    if not ref:
        return "用法: /char alias refresh <selector or provider/selector>"

    if "/" in ref:
        resolved = await resolver.resolve_ref(ref, refresh=True)
        return f"已刷新 {ref}: {resolved.resolved_provider}/{resolved.resolved_model}"

    resolver.refresh(ref)
    current_agent = _current_agent_name(request)
    try:
        current_resolved = await resolver.resolve_agent(current_agent, refresh=True)
    except Exception:
        return f"已刷新 {ref}"
    if current_resolved.selector == ref:
        return f"已刷新 {ref}: {current_resolved.resolved_provider}/{current_resolved.resolved_model}"
    return f"已刷新 {ref}"


async def exec_char_alias_delete(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    ref = request.remaining.strip()
    if not ref:
        return "用法: /char alias delete <selector or provider/selector>"

    resolver.delete(ref)
    return f"已删除 {ref}"


async def exec_char_role_show(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    role_name = request.remaining.strip()
    if not role_name:
        return "用法: /char role show <role>"
    try:
        return await resolver.show_role(role_name)
    except Exception as exc:
        return f"无法显示 role={role_name}: {exc}"


async def exec_char_role_list(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    return resolver.list_roles()


async def exec_char_role_set(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    parts = request.remaining.strip().split(maxsplit=1)
    if len(parts) < 2:
        return "用法: /char role set <role> <selector|provider|provider/selector>"

    role_name, target = parts[0], parts[1].strip()
    if not target:
        return "用法: /char role set <role> <selector|provider|provider/selector>"

    try:
        override = resolver.set_role_override(role_name, target)
        resolved = await resolver.resolve_role(role_name)
    except Exception as exc:
        return f"无法设置 role={role_name}: {exc}"

    override_text = (
        f"provider={override.provider}"
        if override.provider and not override.selector
        else f"selector={override.selector}"
        if override.selector and not override.provider
        else f"provider={override.provider}, selector={override.selector}"
    )
    return (
        f"已更新 role={role_name}: override={override_text} -> "
        f"{resolved.resolved_provider}/{resolved.resolved_model} "
        f"(family={resolved.family or 'unknown'}, vision={resolved.supports_vision})"
    )


async def exec_char_role_refresh(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    role_name = request.remaining.strip()
    if not role_name:
        return "用法: /char role refresh <role>"

    try:
        resolver.refresh_role(role_name)
        resolved = await resolver.resolve_role(role_name, refresh=True)
    except Exception as exc:
        return f"无法刷新 role={role_name}: {exc}"

    return (
        f"已刷新 role={role_name}: "
        f"{resolved.resolved_provider}/{resolved.resolved_model} "
        f"(family={resolved.family or 'unknown'}, vision={resolved.supports_vision})"
    )


async def exec_char_role_clear(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    role_name = request.remaining.strip()
    if not role_name:
        return "用法: /char role clear <role>"

    resolver.clear_role(role_name)
    return f"已清除 role={role_name} 的 runtime override 和 sticky provider"


async def exec_char_selector_list(request: CommandRequest) -> str | None:
    """List known selectors: configured hints + any store bindings."""
    resolver = _resolver(request)
    return resolver.list_selectors()


async def exec_char_list(request: CommandRequest) -> str | None:
    """List all registered characters."""
    del request
    from yuubot.characters import CHARACTER_REGISTRY

    lines = []
    for name, char in CHARACTER_REGISTRY.items():
        lines.append(f"- {name}: {char.description} (min_role={char.min_role})")
    return "\n".join(lines) or "(空)"
