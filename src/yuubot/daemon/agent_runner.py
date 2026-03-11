"""Agent runner — create and run yuuagents Agent for tasks."""

import uuid

from yuubot.characters import CHARACTER_REGISTRY, get_character
from yuubot.commands.tree import MatchResult
from yuubot.config import Config
from yuubot.core import env
from yuubot.core.onebot import parse_segments
from yuubot.daemon.guard import make_whitelist_guard
from yuubot.prompt import (
    RuntimeInfo,
    build_prompt_spec,
    build_system_prompt,
)
from yuubot.skills.im.formatter import (
    format_message_to_xml,
    format_segments,
    get_user_alias,
)

from loguru import logger

# Addon tools — defined in yuubot, registered alongside builtin tools
_ADDON_TOOLS = {}

def _get_addon_tools() -> dict:
    """Lazy-load addon tools to avoid circular imports."""
    global _ADDON_TOOLS
    if not _ADDON_TOOLS:
        from yuubot.addons.tools import execute_addon_cli, read_addon_doc
        _ADDON_TOOLS = {
            "execute_addon_cli": execute_addon_cli,
            "read_addon_doc": read_addon_doc,
        }
    return _ADDON_TOOLS


def _set_or_pop(d: dict, key: str, value: str) -> None:
    """Set key=value in dict, or remove the key if value is empty."""
    if value:
        d[key] = value
    else:
        d.pop(key, None)


# Tools that require a Docker container to function.
_DOCKER_TOOLS = {"execute_bash", "read_file", "write_file", "edit_file", "delete_file"}

_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _file_to_data_uri(path: str) -> str:
    """Read a local file and return a base64 data URI."""
    import base64
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return f"file://{path}"  # fallback
    mime = _MIME_MAP.get(p.suffix.lower(), "application/octet-stream")
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


class AgentRunner:
    """Uses yuuagents SDK to create and run Agent instances."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._initialized = False
        self._docker = None  # DockerManager | None
        self._group_names: dict[int, str] = {}
        self._agent_name_map: dict[str, str] = {}  # runtime_id → config_name
        self._agent_subprocess_env: dict[str, dict] = {}  # agent_id → subprocess env
        self._cli_guard = make_whitelist_guard({"im", "web", "mem", "img", "schedule", "hhsh"})
        self._bot_name: str | None = None  # Cached bot display name
        self._active_flows: dict[int, object] = {}  # ctx_id → root Flow

    def _build_tool_manager(self, tool_names: list[str]):
        """Build a ToolManager with both builtin and addon tools."""
        import yuutools as yt
        from yuuagents import tools as agent_tools

        addon_tools = _get_addon_tools()
        tool_manager = yt.ToolManager()

        # Separate addon tools from builtin tools
        builtin_names = [n for n in tool_names if n not in addon_tools]
        addon_names = [n for n in tool_names if n in addon_tools]

        for t in agent_tools.get(builtin_names):
            tool_manager.register(t)
        for n in addon_names:
            tool_manager.register(addon_tools[n])

        return tool_manager

    def _build_addon_context(
        self,
        *,
        ctx_id: int | str = "",
        user_id: int | str = "",
        user_role: str = "",
        agent_name: str = "",
        task_id: str = "",
    ):
        """Build an AddonContext for in-process addon execution."""
        from yuubot.addons import AddonContext

        return AddonContext(
            config=self.config,
            ctx_id=int(ctx_id) if ctx_id else None,
            user_id=int(user_id) if user_id else None,
            user_role=user_role,
            agent_name=agent_name,
            task_id=task_id,
        )

    @staticmethod
    def _new_task_id() -> str:
        return uuid.uuid4().hex

    def _build_subprocess_env(
        self,
        *,
        task_id: str,
        ctx_id: int | str = "",
        user_id: int | str = "",
        user_role: str = "",
        agent_name: str = "",
        docker_mount: str = "",
        docker_home: str = "",
        docker_home_dir: str = "",
    ) -> dict:
        """Build a subprocess env snapshot with the correct per-run values.

        Taking os.environ as base ensures PATH and other system vars are
        inherited, while we explicitly set/unset the YUU_* keys so that
        concurrent agent runs never see each other's context.
        """
        import os as _os
        e = dict(_os.environ)
        e[env.TASK_ID] = task_id
        e[env.IN_BOT] = "1"
        _set_or_pop(e, env.BOT_CTX, str(ctx_id) if ctx_id else "")
        _set_or_pop(e, env.USER_ID, str(user_id) if user_id else "")
        _set_or_pop(e, env.USER_ROLE, user_role)
        _set_or_pop(e, env.AGENT_NAME, agent_name)
        _set_or_pop(e, env.DOCKER_HOST_MOUNT, docker_mount)
        _set_or_pop(e, env.DOCKER_HOME_HOST_DIR, docker_home)
        _set_or_pop(e, env.DOCKER_HOME_DIR, docker_home_dir)
        return e

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        try:
            from yuuagents.init import setup
            import json
            import msgspec
            from yuuagents.config import Config as YuuagentsConfig
            from yuubot import config as yuubot_config

            base_data = json.loads(msgspec.json.encode(YuuagentsConfig()))
            merged_data = yuubot_config._deep_merge(base_data, self.config.yuuagents)
            cfg = msgspec.convert(merged_data, YuuagentsConfig)
            await setup(cfg)
            self._initialized = True

            # Initialize Docker only if at least one agent uses docker tools
            if self._any_agent_needs_docker():
                try:
                    from yuuagents.daemon.docker import DockerManager

                    self._docker = DockerManager(image=cfg.docker.image)
                    await self._docker.start()
                    logger.info("Docker initialized for AgentRunner")
                except Exception:
                    logger.opt(exception=True).warning(
                        "Docker not available, execute_bash will not work",
                    )
                    self._docker = None
            else:
                logger.info("No agent uses docker tools, skipping Docker initialization")
        except ImportError:
            logger.warning("yuuagents not installed, agent features disabled")
        except Exception:
            logger.exception("Failed to initialize yuuagents")

    async def stop(self) -> None:
        """Shut down Docker and release resources."""
        if self._docker is not None:
            await self._docker.stop()
            self._docker = None

    def get_active_flow(self, ctx_id: int):
        """Return running root flow for ctx, or None."""
        return self._active_flows.get(ctx_id)

    def cancel_ctx(self, ctx_id: int) -> bool:
        """Cancel the active flow for a ctx. Returns True if cancelled."""
        from yuuagents.flow import FlowStatus

        flow = self._active_flows.pop(ctx_id, None)
        if flow is None:
            return False
        if flow.task is not None and not flow.task.done():
            flow.task.cancel()
        flow.status = FlowStatus.CANCELLED
        logger.info("Flow cancelled for ctx={}", ctx_id)
        return True

    async def _resolve_docker(self, task_id: str) -> tuple[str, str]:
        """Return (workdir, container_id) from Docker, or fallback."""
        if self._docker is not None:
            container_id = await self._docker.resolve(task_id=task_id)
            return self._docker.workdir, container_id
        from pathlib import Path

        return str(Path.home()), ""

    async def _docker_home_info(self, container_id: str) -> tuple[str, str, str]:
        """Return docker mount metadata for skill path translation."""
        if self._docker is None or not container_id:
            return "", "", ""

        docker_mount = "/mnt/host"
        container_home = self._docker.container_home
        host_home_dir = await self._docker.host_home_dir(container_id)
        return docker_mount, host_home_dir, container_home

    def _make_summary_llm(self):
        """Build a YLLMClient for summarization/compression.

        Requires explicit summarizer_provider and summarizer_model in SessionConfig.
        """
        import os
        import yuullm

        scfg = self.config.session
        provider_name = scfg.summarizer_provider
        model = scfg.summarizer_model

        if not provider_name or not model:
            raise ValueError(
                "session.summarizer_provider and session.summarizer_model must be "
                "explicitly configured for summarization/compression to work."
            )

        providers = self.config.yuuagents.get("providers", {})
        provider_cfg = providers.get(provider_name, {})
        api_type = provider_cfg.get("api_type", "openai-chat-completion")
        api_key_env = provider_cfg.get("api_key_env", "")
        api_key = os.environ.get(api_key_env) if api_key_env else None
        base_url = provider_cfg.get("base_url", "") or None

        if api_type == "anthropic-messages":
            provider = yuullm.providers.AnthropicMessagesProvider(
                api_key=api_key,
                base_url=base_url,
                provider_name=provider_name,
            )
        else:
            provider = yuullm.providers.OpenAIChatCompletionProvider(
                api_key=api_key,
                base_url=base_url,
                provider_name=provider_name,
            )

        return yuullm.YLLMClient(
            provider=provider,
            default_model=model,
            price_calculator=yuullm.PriceCalculator(),
        )

    def _make_compressor(self, agent_name: str):
        """Build a SessionCompressor for an agent.

        Returns None if summarizer_provider/model not configured.
        """
        scfg = self.config.session
        if not scfg.summarizer_provider or not scfg.summarizer_model:
            return None

        from yuubot.daemon.compressor import SessionCompressor

        llm = self._make_summary_llm()

        async def _summarize_fn(history_slice: list, steps_span: int) -> str:
            from yuubot.daemon.summarizer import compress_summary
            return await compress_summary(history_slice, llm, steps_span=steps_span)

        char = CHARACTER_REGISTRY.get(agent_name)
        if char is None:
            return None

        return SessionCompressor(
            max_tokens=char.max_tokens,
            summarize_fn=_summarize_fn,
            summarize_steps_span=scfg.summarize_steps_span,
        )

    async def summarize(self, history: list, agent_name: str = "main") -> str:
        """Generate a compact handoff note from session history using a cheap LLM."""
        from yuubot.daemon.summarizer import summarize as _summarize

        llm = self._make_summary_llm()
        return await _summarize(history, llm)

    async def curate(self, history: list, ctx_id: int, user_id: int) -> None:
        """Run mem_curator agent to update long-term memories after a session rollover."""
        agent_name = "mem_curator"
        if agent_name not in CHARACTER_REGISTRY:
            logger.debug("mem_curator not configured, skipping")
            return

        await self._ensure_init()

        from yuubot.daemon.summarizer import extract_original_task, render_for_curator

        task = (
            f"以下是本轮 session 的对话摘要，请整理记忆。\n\n"
            f"原始任务：\n{extract_original_task(history)}\n\n"
            f"对话内容：\n{render_for_curator(history)}\n\n"
            f"ctx_id: {ctx_id}\n"
        )
        subprocess_env = self._build_subprocess_env(
            task_id="", ctx_id=ctx_id, user_id=user_id, user_role="MASTER",
        )
        try:
            await self._run_agent(agent_name, task, subprocess_env=subprocess_env)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)

    def _make_llm(self, agent_name: str = "main"):
        """Build a YLLMClient from Character fields, falling back to YAML."""
        import os

        import yuullm

        char = CHARACTER_REGISTRY.get(agent_name)
        provider_name = char.provider if char and char.provider else ""
        model = char.model if char and char.model else ""

        # Fall back to YAML if Character fields are empty
        if not provider_name or not model:
            agents = self.config.yuuagents.get("agents", {})
            agent_cfg = agents.get(agent_name, agents.get("main", {}))
            if not provider_name:
                provider_name = agent_cfg.get("provider", "")
            if not model:
                model = agent_cfg.get("model", "")

        providers = self.config.yuuagents.get("providers", {})
        provider_cfg = providers.get(provider_name, {})

        api_type = provider_cfg.get("api_type", "openai-chat-completion")
        api_key_env = provider_cfg.get("api_key_env", "")
        api_key = os.environ.get(api_key_env) if api_key_env else None
        base_url = provider_cfg.get("base_url", "") or None
        default_model = model or provider_cfg.get("default_model", "gpt-4o")

        if api_type == "anthropic-messages":
            provider = yuullm.providers.AnthropicMessagesProvider(
                api_key=api_key,
                base_url=base_url,
                provider_name=provider_name or "anthropic",
            )
        else:
            provider = yuullm.providers.OpenAIChatCompletionProvider(
                api_key=api_key,
                base_url=base_url,
                provider_name=provider_name or "openai",
            )

        return yuullm.YLLMClient(
            provider=provider,
            default_model=default_model,
            price_calculator=yuullm.PriceCalculator(),
        )

    def _get_runtime(self, agent_name: str) -> RuntimeInfo:
        """Build RuntimeInfo from Character fields, falling back to YAML."""
        char = CHARACTER_REGISTRY.get(agent_name)
        provider_name = char.provider if char and char.provider else ""
        model = char.model if char and char.model else ""

        # Fall back to YAML if Character fields are empty
        if not provider_name or not model:
            agents = self.config.yuuagents.get("agents", {})
            agent_cfg = agents.get(agent_name, {})
            if not provider_name:
                provider_name = agent_cfg.get("provider", "")
            if not model:
                model = agent_cfg.get("model", "")

        providers = self.config.yuuagents.get("providers", {})
        provider_cfg = providers.get(provider_name, {})
        models_cfg = provider_cfg.get("models", {})
        model_cfg = models_cfg.get(model, {})
        supports_vision = bool(model_cfg.get("vision", False))

        return RuntimeInfo(
            provider=provider_name,
            model=model,
            supports_vision=supports_vision,
        )

    def _has_vision(self, agent_name: str) -> bool:
        """Check if the agent's model supports vision."""
        return self._get_runtime(agent_name).supports_vision

    def _build_prompt(self, agent_name: str):
        """Build PromptSpec and SimplePromptBuilder for an agent.

        Returns (prompt_spec, prompt_builder).
        Always uses CHARACTER_REGISTRY as the single source of truth.
        """
        char = get_character(agent_name)
        runtime = self._get_runtime(agent_name)
        prompt_spec = build_prompt_spec(char, runtime, self.config.skill_paths)
        prompt_builder = build_system_prompt(prompt_spec)
        return prompt_spec, prompt_builder

    @staticmethod
    def _needs_docker(tools: list[str]) -> bool:
        """Return True if the tool list includes any docker-dependent tool."""
        return bool(_DOCKER_TOOLS & set(tools))

    def _any_agent_needs_docker(self) -> bool:
        """Return True if any registered character uses docker-dependent tools."""
        for char in CHARACTER_REGISTRY.values():
            if _DOCKER_TOOLS & set(char.spec.tools):
                return True
        return False

    @staticmethod
    def _last_assistant_text(agent) -> str:
        """Extract last assistant text from agent history."""
        from typing import Any

        for msg in reversed(agent.history):
            role: str | None = None
            items: list[Any] | None = None
            if isinstance(msg, tuple) and len(msg) == 2:
                role, items = msg
            if role != "assistant" or not isinstance(items, list):
                continue
            text_parts = [item for item in items if isinstance(item, str)]
            text = "".join(text_parts).strip()
            if text:
                return text
        return ""

    async def _run_agent(
        self,
        agent_name: str,
        task: str,
        *,
        subprocess_env: dict,
        tool_names: list[str] | None = None,
        delegate_depth: int = 0,
        output_buffer=None,
    ) -> str:
        """Launch a configured agent and return its last assistant text.

        subprocess_env must already contain BOT_CTX, USER_ID, USER_ROLE etc.
        Task-specific keys (TASK_ID, AGENT_NAME, DOCKER_*) are set here.
        """
        import yuutools as yt
        from yuuagents import Agent, tools as agent_tools
        from yuuagents.agent import AgentConfig
        from yuuagents.context import AgentContext
        from yuuagents.loop import run as run_agent

        if agent_name not in CHARACTER_REGISTRY:
            raise ValueError(f"Unknown agent {agent_name!r}")

        task_id = self._new_task_id()
        runtime_id = f"agent-{agent_name}-{task_id[:8]}"
        self._agent_name_map[runtime_id] = agent_name

        # Build prompt via character system
        prompt_spec, prompt_builder = self._build_prompt(agent_name)

        # Tools — override if caller specifies
        names = tool_names if tool_names is not None else list(prompt_spec.tools)
        tool_manager = self._build_tool_manager(names)

        needs_docker = self._needs_docker(names)

        # Docker / workdir
        if needs_docker:
            workdir, container_id = await self._resolve_docker(task_id)
        else:
            from pathlib import Path
            workdir, container_id = str(Path.home()), ""

        docker_mount = docker_home = docker_home_dir = ""
        if needs_docker:
            docker_mount, docker_home, docker_home_dir = await self._docker_home_info(
                container_id
            )

        run_env = dict(subprocess_env)
        run_env[env.TASK_ID] = task_id
        run_env[env.IN_BOT] = "1"
        run_env[env.AGENT_NAME] = agent_name
        _set_or_pop(run_env, env.DOCKER_HOST_MOUNT, docker_mount)
        _set_or_pop(run_env, env.DOCKER_HOME_HOST_DIR, docker_home)
        _set_or_pop(run_env, env.DOCKER_HOME_DIR, docker_home_dir)
        self._agent_subprocess_env[runtime_id] = run_env

        agent_spec = prompt_spec.agent_spec
        persona = prompt_spec.resolved_sections[0][1] if prompt_spec.resolved_sections else ""
        compressor = self._make_compressor(agent_name)
        config = AgentConfig(
            task_id=task_id,
            agent_id=runtime_id,
            persona=persona,
            tools=tool_manager,
            llm=self._make_llm(agent_name),
            prompt_builder=prompt_builder,
            max_steps=agent_spec.max_steps,
            soft_timeout=agent_spec.soft_timeout,
            silence_timeout=agent_spec.silence_timeout,
            compressor=compressor,
        )
        agent = Agent(config=config)
        addon_ctx = self._build_addon_context(
            ctx_id=subprocess_env.get(env.BOT_CTX, ""),
            user_id=subprocess_env.get(env.USER_ID, ""),
            user_role=subprocess_env.get(env.USER_ROLE, ""),
            agent_name=agent_name,
            task_id=task_id,
        )
        context = AgentContext(
            task_id=task_id,
            agent_id=runtime_id,
            workdir=workdir,
            docker_container=container_id,
            delegate_depth=delegate_depth,
            manager=self,
            docker=self._docker if needs_docker else None,
            cli_guard=self._cli_guard,
            skill_paths=self.config.skill_paths,
            subprocess_env=run_env,
            current_output_buffer=output_buffer,
            output_buffer=output_buffer,
            addon_context=addon_ctx,
        )

        try:
            await run_agent(agent, task=task, ctx=context)
        finally:
            self._agent_subprocess_env.pop(runtime_id, None)
        return self._last_assistant_text(agent)

    async def delegate(
        self,
        *,
        caller_agent: str,
        agent: str,
        first_user_message: str,
        tools: list[str] | None,
        delegate_depth: int,
        output_buffer=None,
    ) -> str:
        """DelegateManager protocol — run a subagent and return its response."""
        from yuuagents.context import DelegateDepthExceededError

        if delegate_depth > 3:
            raise DelegateDepthExceededError(
                max_depth=3, current_depth=delegate_depth, target_agent=agent,
            )

        # Validate delegation permission
        caller_name = self._agent_name_map.get(caller_agent, caller_agent)

        # Check CHARACTER_REGISTRY for allowed subagents
        allowed = set()
        caller_char = CHARACTER_REGISTRY.get(caller_name)
        if caller_char is not None:
            allowed.update(caller_char.spec.subagents)

        if agent not in allowed:
            raise ValueError(f"Agent {caller_name!r} is not allowed to delegate to {agent!r}")

        parent_env = self._agent_subprocess_env.get(caller_agent, {})
        return await self._run_agent(
            agent,
            first_user_message,
            subprocess_env=dict(parent_env),
            tool_names=tools,
            delegate_depth=delegate_depth,
            output_buffer=output_buffer,
        )

    async def _resolve_group_name(self, group_id: int) -> str:
        """Resolve group_id to group_name, with caching."""
        if group_id in self._group_names:
            return self._group_names[group_id]
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.config.daemon.recorder_api}/get_group_list",
                )
                data = r.json().get("data", r.json())
                if isinstance(data, list):
                    for g in data:
                        self._group_names[g.get("group_id", 0)] = g.get(
                            "group_name", ""
                        )
        except Exception:
            logger.warning("Failed to fetch group list for name resolution")
        return self._group_names.get(group_id, "")

    async def _get_bot_name(self) -> str:
        """Get bot's display name, with caching.

        Falls back to bot QQ number if nickname fetch fails.
        """
        if self._bot_name is not None:
            return self._bot_name
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.config.daemon.recorder_api}/get_login_info",
                )
                data = r.json().get("data", r.json())
                if isinstance(data, dict):
                    nickname = data.get("nickname", "")
                    if nickname:
                        self._bot_name = nickname
                        logger.info("Bot name fetched: {}", nickname)
                        return self._bot_name
        except Exception:
            logger.opt(exception=True).warning("Failed to fetch bot nickname from API")

        # Fallback to bot QQ number
        self._bot_name = str(self.config.bot.qq)
        logger.info("Using bot QQ as name: {}", self._bot_name)
        return self._bot_name

    def _replace_command_prefix(self, segments: list, bot_name: str) -> list:
        """Replace /yllm command prefix with @bot_name in the first text segment.

        Handles: /yllm, /y, /yuu with optional #agent_name suffix.
        Skips leading non-text segments (e.g. ReplySegment) to find the command.
        Returns a new list with modified segments.
        """
        from yuubot.core.models import TextSegment
        import re

        pattern = r"^(/yllm|/yuu|/y)(?:#\w+)?\s*"

        for i, seg in enumerate(segments):
            if not isinstance(seg, TextSegment):
                continue
            text = seg.text.strip()
            match = re.match(pattern, text)
            if match:
                new_text = f"@{bot_name} " + text[match.end():]
                new_segments = list(segments)
                new_segments[i] = TextSegment(text=new_text)
                return new_segments
            # Stop at the first text segment regardless
            break

        return segments


    async def _build_memory_hints(self, text: str, ctx_id: int | str = "") -> str:
        """Probe message text against memory FTS5, return hint string.

        Uses jieba segmentation for accurate Chinese tokenization.
        Best-effort: returns empty string on any failure.
        Filters by ctx_id scope (private + public).
        """
        try:
            from yuubot.skills.mem.store import probe_text

            int_ctx = int(ctx_id) if ctx_id else None
            hits = await probe_text(text, ctx_id=int_ctx)
            if not hits:
                return ""
            return (
                f"\n记忆关键词命中: {', '.join(hits)}\n"
                f'（可用 mem recall "<关键词>" 查看详情）\n'
            )
        except Exception:
            logger.opt(exception=True).debug("Memory hints probe failed")
            return ""

    async def _build_task(
        self,
        match: MatchResult,
        event: dict,
        group_name: str = "",
        *,
        is_continuation: bool = False,
        agent_name: str = "main",
    ) -> str | list:
        """Build agent task description from command match and event.

        Returns str for text-only, or list[Item] when vision is enabled
        and the message contains images.
        """
        ctx_id = event.get("ctx_id", "?")
        segments = parse_segments(event.get("message", []))

        # Strip @bot AtSegments — redundant noise for the LLM
        from yuubot.core.models import AtSegment as _AtSeg
        bot_qq = str(self.config.bot.qq)
        segments = [s for s in segments if not (isinstance(s, _AtSeg) and s.qq == bot_qq)]

        formatted = await format_segments(segments)
        user_id = event.get("user_id", "?")
        nickname = event.get("sender", {}).get("nickname", "")
        msg_type = event.get("message_type", "private")
        msg_text = match.remaining or formatted

        if msg_type == "group":
            group_id = event.get("group_id", "?")
            if group_name:
                location = f"群聊「{group_name}」(group_id={group_id}, ctx={ctx_id})"
            else:
                location = f"群聊 (group_id={group_id}, ctx={ctx_id})"
        else:
            location = f"私聊 (ctx={ctx_id})"

        memory_hints = await self._build_memory_hints(msg_text, ctx_id)

        from datetime import datetime, timezone
        from yuubot.core.models import segments_to_json, TextSegment

        # Replace /yllm command prefix with @bot_name for cleaner LLM history
        bot_name = await self._get_bot_name()
        segments = self._replace_command_prefix(segments, bot_name)

        alias = await get_user_alias(user_id, ctx_id)
        display_name = event.get("sender", {}).get("card", "")
        ts = datetime.fromtimestamp(event.get("time", 0), tz=timezone.utc)
        raw_json = segments_to_json(segments)

        msg_xml = await format_message_to_xml(
            msg_id=event.get("message_id", 0),
            user_id=user_id,
            nickname=nickname,
            display_name=display_name,
            alias=alias,
            timestamp=ts,
            raw_message=raw_json,
            media_files=event.get("media_files", []),
            ctx_id=int(ctx_id) if ctx_id else None,
        )

        # Append extra <msg> blocks from debounced continuation events
        extra_events = event.get("_extra_events", [])
        for extra in extra_events:
            extra_segments = parse_segments(extra.get("message", []))
            extra_segments = self._replace_command_prefix(extra_segments, bot_name)
            extra_user_id = extra.get("user_id", "?")
            extra_nickname = extra.get("sender", {}).get("nickname", "")
            extra_alias = await get_user_alias(extra_user_id, ctx_id)
            extra_display = extra.get("sender", {}).get("card", "")
            extra_ts = datetime.fromtimestamp(extra.get("time", 0), tz=timezone.utc)
            extra_json = segments_to_json(extra_segments)
            extra_xml = await format_message_to_xml(
                msg_id=extra.get("message_id", 0),
                user_id=extra_user_id,
                nickname=extra_nickname,
                display_name=extra_display,
                alias=extra_alias,
                timestamp=extra_ts,
                raw_message=extra_json,
                media_files=extra.get("media_files", []),
                ctx_id=int(ctx_id) if ctx_id else None,
            )
            msg_xml += "\n" + extra_xml

        if is_continuation:
            total_msgs = 1 + len(extra_events)
            count_hint = f"你收到了{total_msgs}条新消息:\n" if total_msgs > 1 else ""
            text = f"""{count_hint}{msg_xml}
{memory_hints}"""
        else:
            text = f"""你收到了来自{location}的消息。
{msg_xml}
{memory_hints}
回复时使用 im send 命令发送到 ctx {ctx_id}。遇到奇怪的问题时可使用im工具查看上下文。你自己生成的回复不会被看到，简单输出结束即可。
"""

        # Vision: if the model supports vision and message has images,
        # build multimodal content items
        if not self._has_vision(agent_name):
            return text

        from yuubot.core.models import ImageSegment

        image_segments = [s for s in segments if isinstance(s, ImageSegment)]
        for extra in event.get("_extra_events", []):
            extra_segs = parse_segments(extra.get("message", []))
            image_segments.extend(s for s in extra_segs if isinstance(s, ImageSegment))
        if not image_segments:
            return text

        from yuullm import ImageItem, TextItem

        items: list = [TextItem(type="text", text=text)]
        for seg in image_segments:
            url = ""
            if seg.local_path:
                url = _file_to_data_uri(seg.local_path)
            elif seg.url:
                url = seg.url
            if url:
                items.append(ImageItem(type="image_url", image_url={"url": url}))

        return items if len(items) > 1 else text

    async def run(
        self,
        match: MatchResult,
        event: dict,
        *,
        agent_name: str = "main",
        user_role: str = "",
        session: object | None = None,
    ) -> tuple[list, int, str]:
        """Passive mode: handle a command-triggered agent task.

        Returns (history, total_tokens, task_id) for session management.
        """
        await self._ensure_init()

        try:
            from yuuagents import Agent
            from yuuagents.agent import AgentConfig
            from yuuagents.loop import run as run_agent
            from yuuagents.context import AgentContext
        except ImportError:
            logger.error("yuuagents not available, cannot run agent")
            return [], 0, ""

        ctx_id = event.get("ctx_id", 0)
        user_id = event.get("user_id", 0)
        is_continuation = session is not None and bool(
            getattr(session, "history", None)
        )
        task_id = (
            session.task_id
            if is_continuation and getattr(session, "task_id", "")
            else self._new_task_id()
        )

        # Resolve group name for context
        group_name = ""
        if event.get("message_type") == "group":
            group_name = await self._resolve_group_name(event.get("group_id", 0))

        # Build prompt via character system
        prompt_spec, prompt_builder = self._build_prompt(agent_name)
        agent_spec = prompt_spec.agent_spec
        tool_names = list(prompt_spec.tools)

        tool_manager = self._build_tool_manager(tool_names)

        needs_docker = self._needs_docker(tool_names)

        agent_id = f"yuubot-{agent_name}-{ctx_id}"
        self._agent_name_map[agent_id] = agent_name

        persona = prompt_spec.resolved_sections[0][1] if prompt_spec.resolved_sections else ""
        compressor = self._make_compressor(agent_name)
        config = AgentConfig(
            task_id=task_id,
            agent_id=agent_id,
            persona=persona,
            tools=tool_manager,
            llm=self._make_llm(agent_name),
            prompt_builder=prompt_builder,
            max_steps=agent_spec.max_steps,
            soft_timeout=agent_spec.soft_timeout,
            silence_timeout=agent_spec.silence_timeout,
            compressor=compressor,
        )

        agent = Agent(config=config)
        task = await self._build_task(
            match,
            event,
            group_name=group_name,
            is_continuation=is_continuation,
            agent_name=agent_name,
        )

        # Inject handoff note from a rolled-over session into the task text
        handoff_note = getattr(session, "handoff_note", "") if session else ""
        if handoff_note and not is_continuation:
            note_block = f"<上轮对话摘要>\n{handoff_note}\n</上轮对话摘要>\n\n"
            if isinstance(task, str):
                task = note_block + task
            elif (
                isinstance(task, list)
                and task
                and isinstance(task[0], dict)
                and task[0].get("type") == "text"
            ):
                task[0] = {"type": "text", "text": note_block + task[0]["text"]}

        # Determine text form for run_agent's task param (always str)
        task_str = task if isinstance(task, str) else task[0]["text"] if task else ""
        is_multimodal = isinstance(task, list)

        if is_continuation:
            from yuuagents.agent import AgentStatus

            agent.state.history = list(session.history)
            user_items = task if is_multimodal else [task_str]
            agent.state.history.append(("user", user_items))
            agent.state.status = AgentStatus.RUNNING
            agent.state.task = task_str
        elif is_multimodal:
            # Non-continuation multimodal: manually build history
            import yuullm
            from yuuagents.agent import AgentStatus

            agent.state.history = [
                yuullm.system(agent.full_system_prompt),
                ("user", task),
            ]
            agent.state.status = AgentStatus.RUNNING
            agent.state.task = task_str
        if needs_docker:
            workdir, container_id = await self._resolve_docker(task_id)
        else:
            from pathlib import Path

            workdir, container_id = str(Path.home()), ""

        docker_mount = ""
        docker_home = ""
        docker_home_dir = ""
        if needs_docker:
            docker_mount, docker_home, docker_home_dir = await self._docker_home_info(
                container_id
            )

        subprocess_env = self._build_subprocess_env(
            task_id=task_id,
            ctx_id=ctx_id,
            user_id=user_id,
            user_role=user_role,
            agent_name=agent_name,
            docker_mount=docker_mount,
            docker_home=docker_home,
            docker_home_dir=docker_home_dir,
        )
        self._agent_subprocess_env[agent_id] = subprocess_env

        addon_ctx = self._build_addon_context(
            ctx_id=ctx_id,
            user_id=user_id,
            user_role=user_role,
            agent_name=agent_name,
            task_id=task_id,
        )
        context = AgentContext(
            task_id=task_id,
            agent_id=agent_id,
            workdir=workdir,
            docker_container=container_id,
            docker=self._docker if needs_docker else None,
            manager=self,
            cli_guard=self._cli_guard,
            skill_paths=self.config.skill_paths,
            subprocess_env=subprocess_env,
            addon_context=addon_ctx,
        )

        from yuuagents.flow import FlowManager, FlowKind

        flow_manager = FlowManager()
        root_flow = flow_manager.create(FlowKind.AGENT, name=agent_id)
        self._active_flows[ctx_id] = root_flow

        logger.info(
            "agent start: ctx={} agent={} task_id={} continuation={}",
            ctx_id, agent_name, task_id, is_continuation,
        )
        try:
            if is_continuation or is_multimodal:
                await run_agent(agent, task=task_str, ctx=context, resume=True,
                                flow_manager=flow_manager, root_flow=root_flow)
            else:
                await run_agent(agent, task=task_str, ctx=context,
                                flow_manager=flow_manager, root_flow=root_flow)
        except BaseException:
            logger.exception("agent failed: ctx={} agent={} task_id={}", ctx_id, agent_name, task_id)
        finally:
            self._active_flows.pop(ctx_id, None)
            self._agent_subprocess_env.pop(agent_id, None)

        logger.info(
            "agent done: ctx={} agent={} task_id={} tokens={}",
            ctx_id, agent_name, task_id, agent.total_tokens,
        )
        return list(agent.history), agent.total_tokens, task_id

    async def run_scheduled(
        self, task: str, ctx_id: int | None, *, agent_name: str = "main"
    ) -> None:
        """Active mode: run a scheduled agent task."""
        await self._ensure_init()

        try:
            from yuuagents import Agent
            from yuuagents.agent import AgentConfig
            from yuuagents.loop import run as run_agent
            from yuuagents.context import AgentContext
        except ImportError:
            logger.error("yuuagents not available")
            return

        task_id = self._new_task_id()

        # Build prompt via character system
        prompt_spec, prompt_builder = self._build_prompt(agent_name)
        agent_spec = prompt_spec.agent_spec
        tool_names = list(prompt_spec.tools)

        tool_manager = self._build_tool_manager(tool_names)

        needs_docker = self._needs_docker(tool_names)

        ctx_str = f"ctx {ctx_id}" if ctx_id else "无指定 ctx"
        full_task = f"""定时任务触发。
任务: {task}
目标: {ctx_str}

如需发送消息，使用 im send 命令发送到对应 ctx。
"""

        agent_id = f"yuubot-cron-{agent_name}-{ctx_id or 'global'}"
        self._agent_name_map[agent_id] = agent_name

        persona = prompt_spec.resolved_sections[0][1] if prompt_spec.resolved_sections else ""
        compressor = self._make_compressor(agent_name)
        config = AgentConfig(
            task_id=task_id,
            agent_id=agent_id,
            persona=persona,
            tools=tool_manager,
            llm=self._make_llm(agent_name),
            prompt_builder=prompt_builder,
            max_steps=agent_spec.max_steps,
            soft_timeout=agent_spec.soft_timeout,
            silence_timeout=agent_spec.silence_timeout,
            compressor=compressor,
        )

        agent = Agent(config=config)
        if needs_docker:
            workdir, container_id = await self._resolve_docker(task_id)
        else:
            from pathlib import Path

            workdir, container_id = str(Path.home()), ""

        docker_mount = ""
        docker_home = ""
        docker_home_dir = ""
        if needs_docker:
            docker_mount, docker_home, docker_home_dir = await self._docker_home_info(
                container_id
            )

        subprocess_env = self._build_subprocess_env(
            task_id=task_id,
            ctx_id=ctx_id or "",
            agent_name=agent_name,
            docker_mount=docker_mount,
            docker_home=docker_home,
            docker_home_dir=docker_home_dir,
        )
        self._agent_subprocess_env[agent_id] = subprocess_env

        addon_ctx = self._build_addon_context(
            ctx_id=ctx_id or "",
            agent_name=agent_name,
            task_id=task_id,
        )
        context = AgentContext(
            task_id=task_id,
            agent_id=agent_id,
            workdir=workdir,
            docker_container=container_id,
            docker=self._docker if needs_docker else None,
            manager=self,
            cli_guard=self._cli_guard,
            skill_paths=self.config.skill_paths,
            subprocess_env=subprocess_env,
            addon_context=addon_ctx,
        )

        try:
            await run_agent(agent, task=full_task, ctx=context)
        except BaseException:
            logger.exception("Scheduled agent failed: {}", task)
        finally:
            self._agent_subprocess_env.pop(agent_id, None)
