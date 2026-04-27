"""Character inspection and selector-based runtime config mutation."""

from __future__ import annotations

from yuubot.commands.tree import CommandRequest
from yuubot.model_resolution import ModelResolver


def _resolver(request: CommandRequest) -> ModelResolver:
    return ModelResolver(request.deps["config"])


def _current_agent_name(request: CommandRequest) -> str:
    session_mgr = request.deps.get("session_mgr")
    ctx_id = request.message.ctx_id
    if session_mgr is not None and ctx_id:
        current_agent = session_mgr.current_agent(ctx_id)
        if current_agent:
            return current_agent
        conv = session_mgr.get(ctx_id)
        if conv is not None and conv.agent_name:
            return conv.agent_name
    return "yuu"


async def exec_char_show_prompt(request: CommandRequest) -> str | None:
    """Show the rendered system prompt for a character."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character
    from yuubot.daemon.runtime import YuubotRuntimeFactory
    from yuubot.prompt import render_system_prompt

    name = request.remaining.strip() or "yuu"
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    character = get_character(name)
    factory = YuubotRuntimeFactory(request.deps["config"])
    delegates = factory._delegate_descriptions(character)
    return render_system_prompt(character, delegate_descriptions=delegates)


async def exec_char_show_config(request: CommandRequest) -> str | None:
    """Show character config and current model resolution state."""
    from yuubot.characters import CHARACTER_REGISTRY, get_character

    name = request.remaining.strip() or "yuu"
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    character = get_character(name)
    resolver = _resolver(request)
    ref = resolver.get_agent_llm_ref(name)
    try:
        resolved_text = (await resolver.resolve_agent(name)).resolved_ref
    except Exception as exc:
        resolved_text = f"解析失败: {exc}"
    spec = character.spec
    delegate_policy = spec.delegate_policy

    lines = [
        f"Character: {character.name}",
        f"Description: {character.description}",
        f"Bot kind: {character.bot_kind}",
        f"LLM ref: {ref}",
        f"Resolved: {resolved_text}",
        "",
        f"Facade: {spec.facade_module}",
        f"Imports: {', '.join(str(item) for item in spec.resolved_imports())}",
        f"Expand functions: {', '.join(spec.expand_functions) or '(none)'}",
        f"Max turns: {spec.max_turns}",
        f"Inactivity timeout: {spec.inactivity_timeout_s or '(none)'}",
    ]
    if delegate_policy is not None:
        lines.append(
            "Delegates: "
            + (
                ", ".join(delegate_policy.allowed_agents)
                if delegate_policy.allowed_agents
                else "(none)"
            )
        )
    return "\n".join(lines)


async def exec_char_config(request: CommandRequest) -> str | None:
    """Hot-swap a character's in-memory LLM ref."""
    from yuubot.characters import CHARACTER_REGISTRY

    parts = request.remaining.strip().split()
    if not parts:
        return "用法: /ychar config <name> llm=<provider>/<selector-or-model>"

    name = parts[0]
    if name not in CHARACTER_REGISTRY:
        return f"未知 Character: {name}\n可用: {', '.join(CHARACTER_REGISTRY)}"

    llm_ref = ""
    for item in parts[1:]:
        if item.startswith("llm="):
            llm_ref = item.split("=", 1)[1].strip()
            break
    if not llm_ref:
        return "用法: /ychar config <name> llm=<provider>/<selector-or-model>"

    resolver = _resolver(request)
    try:
        resolved = await resolver.resolve_ref(llm_ref)
    except Exception as exc:
        return f"无法解析 llm={llm_ref}: {exc}"

    config_refs = request.deps["config"].agent_llm_refs

    # Line 111 area — check type
    if not isinstance(config_refs, dict):
        return "当前配置不支持 agent_llm_refs 热更新"
    config_refs[name] = llm_ref
    return (
        f"已更新 {name}: llm={llm_ref} -> {resolved.resolved_ref} "
        f"(family={resolved.family or 'unknown'}, vision={resolved.supports_vision})\n"
        "提示: 已影响后续新会话；已有会话请 /yclose 后再继续。"
    )


async def exec_char_alias(request: CommandRequest) -> str | None:
    """Create a persistent selector binding."""
    text = request.remaining.strip()
    if not text or " as " not in text:
        return (
            "用法: /ychar alias <provider>/<model> as <selector>\n"
            "     /ychar alias * as <selector>"
        )

    source_raw, selector = [part.strip() for part in text.split(" as ", 1)]
    if not selector:
        return "selector 不能为空"

    resolver = _resolver(request)
    if source_raw == "*":
        agent_name = _current_agent_name(request)
        try:
            resolved = await resolver.bind_current(agent_name, selector)
        except Exception as exc:
            return f"无法绑定当前模型为 {selector}: {exc}"
        return f"已绑定 {selector}: {resolved.resolved_ref} (from {agent_name})"

    try:
        resolved_source = await resolver.resolve_ref(source_raw)
        bound = resolver.bind_resolved(resolved_source, selector)
    except Exception as exc:
        return f"无法绑定 {source_raw} as {selector}: {exc}"
    return f"已绑定 {selector}: {bound.resolved_ref}"


async def exec_char_alias_show(request: CommandRequest) -> str | None:
    selector = request.remaining.strip()
    if not selector:
        return "用法: /ychar alias show <selector>"
    return _resolver(request).show_selector(selector)


async def exec_char_alias_refresh(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    ref = request.remaining.strip()
    if not ref:
        return "用法: /ychar alias refresh <selector or provider/selector>"

    if "/" in ref:
        try:
            resolved = await resolver.resolve_ref(ref, refresh=True)
        except Exception as exc:
            return f"无法刷新 {ref}: {exc}"
        return f"已刷新 {ref}: {resolved.resolved_ref}"

    resolver.refresh(ref)
    return f"已刷新 {ref}"


async def exec_char_alias_delete(request: CommandRequest) -> str | None:
    ref = request.remaining.strip()
    if not ref:
        return "用法: /ychar alias delete <selector or provider/selector>"
    _resolver(request).delete(ref)
    return f"已删除 {ref}"


async def exec_char_role_list(request: CommandRequest) -> str | None:
    return _resolver(request).list_roles()


async def exec_char_role_show(request: CommandRequest) -> str | None:
    role_name = request.remaining.strip()
    if not role_name:
        return "用法: /ychar role show <role>"
    try:
        return await _resolver(request).show_role(role_name)
    except Exception as exc:
        return f"无法显示 role={role_name}: {exc}"


async def exec_char_role_set(request: CommandRequest) -> str | None:
    resolver = _resolver(request)
    parts = request.remaining.strip().split(maxsplit=1)
    if len(parts) < 2:
        return "用法: /ychar role set <role> <selector|provider|provider/selector>"

    role_name, target = parts[0], parts[1].strip()
    if not target:
        return "用法: /ychar role set <role> <selector|provider|provider/selector>"

    try:
        override = resolver._normalize_role_target(target)
        provider, selector = resolver._role_target(role_name)
        if override.provider:
            provider = override.provider
        if override.selector:
            selector = override.selector
        ref = f"{provider}/{selector}" if provider else selector
        config_roles = request.deps["config"].llm_roles
        if not isinstance(config_roles, dict):
            return "当前配置不支持 llm_roles 热更新"
        config_roles[role_name] = ref
        resolved = await ModelResolver(request.deps["config"]).resolve_role(role_name)
    except Exception as exc:
        return f"无法设置 role={role_name}: {exc}"

    return (
        f"已更新 role={role_name}: {ref} -> {resolved.resolved_ref} "
        f"(family={resolved.family or 'unknown'}, vision={resolved.supports_vision})"
    )


async def exec_char_role_refresh(request: CommandRequest) -> str | None:
    role_name = request.remaining.strip()
    if not role_name:
        return "用法: /ychar role refresh <role>"

    resolver = _resolver(request)
    try:
        resolver.refresh_role(role_name)
        resolved = await resolver.resolve_role(role_name, refresh=True)
    except Exception as exc:
        return f"无法刷新 role={role_name}: {exc}"
    return f"已刷新 role={role_name}: {resolved.resolved_ref}"


async def exec_char_role_clear(request: CommandRequest) -> str | None:
    role_name = request.remaining.strip()
    if not role_name:
        return "用法: /ychar role clear <role>"
    _resolver(request).clear_role(role_name)
    return f"已清除 role={role_name} 的 runtime override 和 sticky provider"


async def exec_char_selector_list(request: CommandRequest) -> str | None:
    return _resolver(request).list_selectors()


async def exec_char_list(request: CommandRequest) -> str | None:
    del request
    from yuubot.characters import CHARACTER_REGISTRY

    lines = [
        f"- {name}: {character.description} (bot_kind={character.bot_kind})"
        for name, character in CHARACTER_REGISTRY.items()
    ]
    return "\n".join(lines) or "(空)"
