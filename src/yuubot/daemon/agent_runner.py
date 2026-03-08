"""Agent runner — create and run yuuagents Agent for tasks."""
import logging
import uuid

from yuubot.commands.tree import MatchResult
from yuubot.config import Config
from yuubot.core import env
from yuubot.core.onebot import parse_segments
from yuubot.daemon.guard import make_whitelist_guard
from yuubot.skills.im.formatter import format_message_to_xml, format_segments, get_user_alias

log = logging.getLogger(__name__)

# Tools that require a Docker container to function.
_DOCKER_TOOLS = {"execute_bash", "read_file", "write_file", "delete_file"}

_MIME_MAP = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


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


class _SessionManagerBridge:
    """Bridges yuuagents SessionRegistry to the SessionManager protocol expected by tools."""

    def __init__(self, runner: "AgentRunner") -> None:
        self._runner = runner

    async def launch(self, *, caller_agent: str, agent: str, task: str, context: str) -> str:
        from yuuagents.session import SessionRegistry

        registry = self._runner._session_registry
        if registry is None:
            raise RuntimeError("SessionRegistry not initialized")

        # Create agent + context for the session
        runner = self._runner
        await runner._ensure_init()

        import yuutools as yt
        from yuuagents import Agent, tools as agent_tools
        from yuuagents.agent import AgentConfig, SimplePromptBuilder
        from yuuagents.context import AgentContext
        from yuuagents.prompts import get_vars as get_prompt_vars

        agents_cfg = runner.config.yuuagents.get("agents", {})
        if agent not in agents_cfg:
            raise ValueError(f"Unknown agent {agent!r}")

        # Validate caller permission
        caller_name = runner._agent_name_map.get(caller_agent, caller_agent)
        caller_cfg = agents_cfg.get(caller_name, {})
        allowed = caller_cfg.get("subagents", [])
        if "*" not in allowed and agent not in allowed:
            raise ValueError(f"Agent {caller_name!r} cannot launch {agent!r}")

        task_id = runner._new_task_id()
        tool_names = runner._resolve_tool_names(agent)
        tool_manager = yt.ToolManager()
        for t in agent_tools.get(tool_names):
            tool_manager.register(t)

        target_cfg = agents_cfg[agent]
        persona = target_cfg.get("persona", "")

        # Apply prompt variable substitution
        prompt_vars = get_prompt_vars()
        for key, value in prompt_vars.items():
            persona = persona.replace(f"{{{key}}}", value)

        prompt_builder = SimplePromptBuilder()
        prompt_builder.add_section(persona)

        needs_docker = runner._agent_needs_docker(agent)
        if needs_docker and runner._docker is not None:
            from yuuagents.daemon.docker import DOCKER_SYSTEM_PROMPT
            if DOCKER_SYSTEM_PROMPT:
                prompt_builder.add_section(DOCKER_SYSTEM_PROMPT)

        full_task = task if not context.strip() else f"context:\n{context}\n\ntask:\n{task}"

        config = AgentConfig(
            task_id=task_id,
            agent_id=f"session-{agent}-{task_id[:8]}",
            persona=persona,
            tools=tool_manager,
            llm=runner._make_llm(agent),
            prompt_builder=prompt_builder,
            max_steps=runner._get_max_steps(agent),
        )
        sub_agent = Agent(config=config)

        if needs_docker:
            workdir, container_id = await runner._resolve_docker(task_id)
        else:
            from pathlib import Path
            workdir, container_id = str(Path.home()), ""

        ctx = AgentContext(
            task_id=task_id,
            agent_id=config.agent_id,
            workdir=workdir,
            docker_container=container_id,
            docker=runner._docker if needs_docker else None,
            manager=runner,
            session_manager=self,
            cli_guard=runner._cli_guard,
        )

        session = registry.create(
            sub_agent, ctx, full_task,
            on_complete=runner._on_session_complete,
        )
        await session.start()
        return session.session_id

    def poll(self, session_id: str) -> dict:
        registry = self._runner._session_registry
        if registry is None:
            return {"status": "error", "progress": [], "elapsed": 0}
        session = registry.get(session_id)
        if session is None:
            return {"status": "not_found", "progress": [], "elapsed": 0}
        return {
            "status": session.status,
            "progress": session.progress(),
            "elapsed": session.elapsed,
        }

    async def interrupt(self, session_id: str) -> str:
        registry = self._runner._session_registry
        if registry is None:
            return "SessionRegistry not initialized"
        session = registry.get(session_id)
        if session is None:
            return f"Session {session_id} not found"
        await session.interrupt()
        return f"Session {session_id} interrupted"

    def result(self, session_id: str) -> str | None:
        registry = self._runner._session_registry
        if registry is None:
            return None
        session = registry.get(session_id)
        if session is None:
            return None
        return session.result()


class AgentRunner:
    """Uses yuuagents SDK to create and run Agent instances."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._initialized = False
        self._docker = None  # DockerManager | None
        self._group_names: dict[int, str] = {}
        self._agent_name_map: dict[str, str] = {}  # runtime_id → config_name
        self._cli_guard = make_whitelist_guard({"im", "web", "mem", "img", "schedule"})
        self._session_registry = None  # SessionRegistry | None
        self._session_bridge = _SessionManagerBridge(self)
        self._on_session_complete_callback = None  # callable | None

    @staticmethod
    def _new_task_id() -> str:
        return uuid.uuid4().hex

    def set_on_session_complete(self, callback) -> None:
        """Set callback for when an agent session completes."""
        self._on_session_complete_callback = callback

    async def _on_session_complete(self, session_id: str, result: str | None, error: str | None) -> None:
        """Called when an AgentSession finishes."""
        log.info("Agent session %s completed (error=%s)", session_id, error)
        if self._on_session_complete_callback is not None:
            await self._on_session_complete_callback(session_id, result, error)

    def has_active_sessions(self) -> bool:
        """Return True if there are active agent sessions."""
        if self._session_registry is None:
            return False
        return bool(self._session_registry.list_active())

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        try:
            from yuuagents.init import setup
            from yuuagents.session import SessionRegistry
            import json
            import msgspec
            from yuuagents.config import Config as YuuagentsConfig
            from yuubot import config as yuubot_config

            base_data = json.loads(msgspec.json.encode(YuuagentsConfig()))
            merged_data = yuubot_config._deep_merge(base_data, self.config.yuuagents)
            cfg = msgspec.convert(merged_data, YuuagentsConfig)
            await setup(cfg)
            self._initialized = True
            self._session_registry = SessionRegistry()

            # Initialize Docker only if at least one agent uses docker tools
            if self._any_agent_needs_docker():
                try:
                    from yuuagents.daemon.docker import DockerManager

                    self._docker = DockerManager(image=cfg.docker.image)
                    await self._docker.start()
                    log.info("Docker initialized for AgentRunner")
                except Exception:
                    log.warning("Docker not available, execute_bash will not work", exc_info=True)
                    self._docker = None
            else:
                log.info("No agent uses docker tools, skipping Docker initialization")
        except ImportError:
            log.warning("yuuagents not installed, agent features disabled")
        except Exception:
            log.exception("Failed to initialize yuuagents")

    async def stop(self) -> None:
        """Shut down Docker, sessions, and release resources."""
        if self._session_registry is not None:
            await self._session_registry.stop_all()
            self._session_registry = None
        if self._docker is not None:
            await self._docker.stop()
            self._docker = None

    async def _resolve_docker(self, task_id: str) -> tuple[str, str]:
        """Return (workdir, container_id) from Docker, or fallback."""
        if self._docker is not None:
            container_id = await self._docker.resolve(task_id=task_id)
            return self._docker.workdir, container_id
        from pathlib import Path
        return str(Path.home()), ""

    def _make_llm(self, agent_name: str = "main"):
        """Build a YLLMClient from yuuagents provider config."""
        import os

        import yuullm

        agents = self.config.yuuagents.get("agents", {})
        agent_cfg = agents.get(agent_name, agents.get("main", {}))
        provider_name = agent_cfg.get("provider", "")
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

    def _has_vision(self, agent_name: str) -> bool:
        """Check if the agent's model supports vision.

        Looks up providers[provider].models[model].vision from config.
        """
        agents = self.config.yuuagents.get("agents", {})
        agent_cfg = agents.get(agent_name, {})
        provider_name = agent_cfg.get("provider", "")
        model = agent_cfg.get("model", "")

        providers = self.config.yuuagents.get("providers", {})
        provider_cfg = providers.get(provider_name, {})
        models_cfg = provider_cfg.get("models", {})
        model_cfg = models_cfg.get(model, {})
        return bool(model_cfg.get("vision", False))

    def _get_persona(self, agent_name: str) -> str:
        """Get persona string for the given agent name."""
        agents = self.config.yuuagents.get("agents", {})
        agent_cfg = agents.get(agent_name, {})
        return agent_cfg.get("persona", "你是一个有用的QQ机器人助手。")

    def _get_max_steps(self, agent_name: str) -> int:
        """Get max_steps for the given agent name. 0 = unlimited."""
        agents = self.config.yuuagents.get("agents", {})
        agent_cfg = agents.get(agent_name, {})
        return int(agent_cfg.get("max_steps", 0))

    def _load_skills_docs(self, agent_name: str = "main") -> str:
        """Load SKILL.md files and render into prompt section.

        Skills listed in the agent's ``expand_skills`` config have their full
        SKILL.md content inlined into the prompt so the LLM doesn't need an
        extra tool call to read them.
        """
        try:
            from yuuagents.skills import scan, render

            all_skills = scan(self.config.skill_paths)
            if not all_skills:
                return ""

            agents_cfg = self.config.yuuagents.get("agents", {})
            expand_names = set(agents_cfg.get(agent_name, {}).get("expand_skills", []))

            expanded = []
            remaining = []
            for s in all_skills:
                if s.name in expand_names:
                    expanded.append(s)
                else:
                    remaining.append(s)

            parts: list[str] = []

            # Inline full SKILL.md for expanded skills
            for s in expanded:
                try:
                    from pathlib import Path
                    content = Path(s.location).read_text(encoding="utf-8")
                    parts.append(
                        f"<skill_doc name=\"{s.name}\">\n{content}\n</skill_doc>"
                    )
                except Exception:
                    log.warning("Failed to read SKILL.md for %s at %s", s.name, s.location)
                    remaining.append(s)  # fallback to summary

            # Summary for the rest
            summary = render(remaining)
            if summary:
                parts.append(summary)

            return "\n\n".join(parts)
        except ImportError:
            return ""
        except Exception:
            log.exception("Failed to load skills docs")
            return ""

    def _resolve_tool_names(self, agent_name: str) -> list[str]:
        """Get tool names for an agent, adding 'delegate' if subagents configured."""
        agents_cfg = self.config.yuuagents.get("agents", {})
        agent_cfg = agents_cfg.get(agent_name, {})
        tool_names = list(agent_cfg.get("tools", []))
        subagents = agent_cfg.get("subagents", [])
        if subagents and "delegate" not in tool_names:
            tool_names.append("delegate")
        return tool_names

    def _agent_needs_docker(self, agent_name: str) -> bool:
        """Return True if the agent uses any docker-dependent tool."""
        return bool(_DOCKER_TOOLS & set(self._resolve_tool_names(agent_name)))

    def _any_agent_needs_docker(self) -> bool:
        """Return True if any configured agent uses docker-dependent tools."""
        agents_cfg = self.config.yuuagents.get("agents", {})
        return any(self._agent_needs_docker(name) for name in agents_cfg)

    def _build_subagents_prompt(self, agent_name: str) -> str:
        """Generate prompt section listing available subagents."""
        agents_cfg = self.config.yuuagents.get("agents", {})
        agent_cfg = agents_cfg.get(agent_name, {})
        subagents = agent_cfg.get("subagents", [])
        if not subagents:
            return ""

        if "*" in subagents:
            targets = [n for n in agents_cfg if n != agent_name]
        else:
            targets = [n for n in subagents if n in agents_cfg]

        if not targets:
            return ""

        lines = [
            "<agents>",
            "以下是其他可调用的 Agent。需要时使用 delegate 工具调用。",
        ]
        for name in targets:
            desc = agents_cfg[name].get("description", "").strip()
            lines.append(f"- name: {name}")
            if desc:
                lines.append(f"  description: {desc}")
        lines.append("</agents>")
        return "\n".join(lines)

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

    async def delegate(
        self,
        *,
        caller_agent: str,
        agent: str,
        first_user_message: str,
        tools: list[str] | None,
        delegate_depth: int,
    ) -> str:
        """DelegateManager protocol — run a subagent and return its response."""
        import yuutools as yt
        from yuuagents import Agent, tools as agent_tools
        from yuuagents.agent import AgentConfig, SimplePromptBuilder
        from yuuagents.context import AgentContext, DelegateDepthExceededError
        from yuuagents.loop import run as run_agent

        if delegate_depth > 3:
            raise DelegateDepthExceededError(
                max_depth=3,
                current_depth=delegate_depth,
                target_agent=agent,
            )

        agents_cfg = self.config.yuuagents.get("agents", {})

        # Validate caller permission
        caller_name = self._agent_name_map.get(caller_agent, caller_agent)
        caller_cfg = agents_cfg.get(caller_name, {})
        allowed = caller_cfg.get("subagents", [])
        if "*" not in allowed and agent not in allowed:
            raise ValueError(
                f"Agent {caller_name!r} is not allowed to delegate to {agent!r}"
            )

        if agent not in agents_cfg:
            raise ValueError(f"Unknown agent {agent!r}")

        # Resolve tools for target agent
        tool_names = tools or self._resolve_tool_names(agent)
        tool_manager = yt.ToolManager()
        for t in agent_tools.get(tool_names):
            tool_manager.register(t)

        # Build prompt
        target_cfg = agents_cfg[agent]
        persona = target_cfg.get("persona", "")
        prompt_builder = SimplePromptBuilder()
        prompt_builder.add_section(persona)
        subagents_prompt = self._build_subagents_prompt(agent)
        if subagents_prompt:
            prompt_builder.add_section(subagents_prompt)
        needs_docker = self._agent_needs_docker(agent)
        if needs_docker and self._docker is not None:
            from yuuagents.daemon.docker import DOCKER_SYSTEM_PROMPT
            if DOCKER_SYSTEM_PROMPT:
                prompt_builder.add_section(DOCKER_SYSTEM_PROMPT)

        task_id = self._new_task_id()
        runtime_id = f"delegate-{agent}-{task_id[:8]}"
        self._agent_name_map[runtime_id] = agent

        config = AgentConfig(
            task_id=task_id,
            agent_id=runtime_id,
            persona=persona,
            tools=tool_manager,
            llm=self._make_llm(agent),
            prompt_builder=prompt_builder,
            max_steps=self._get_max_steps(agent),
        )
        sub_agent = Agent(config=config)
        if needs_docker:
            workdir, container_id = await self._resolve_docker(task_id)
        else:
            from pathlib import Path
            workdir, container_id = str(Path.home()), ""
        context = AgentContext(
            task_id=task_id,
            agent_id=runtime_id,
            workdir=workdir,
            docker_container=container_id,
            delegate_depth=delegate_depth,
            manager=self,
            docker=self._docker if needs_docker else None,
            cli_guard=self._cli_guard,
        )

        with env.task_env(task_id=task_id):
            await run_agent(sub_agent, task=first_user_message, ctx=context)
        return self._last_assistant_text(sub_agent)

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
            log.warning("Failed to fetch group list for name resolution")
        return self._group_names.get(group_id, "")

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
                f"（可用 ybot mem recall \"<关键词>\" 查看详情）\n"
            )
        except Exception:
            log.debug("Memory hints probe failed", exc_info=True)
            return ""

    async def _build_task(
        self, match: MatchResult, event: dict, group_name: str = "",
        *, is_continuation: bool = False, agent_name: str = "main",
    ) -> str | list:
        """Build agent task description from command match and event.

        Returns str for text-only, or list[Item] when vision is enabled
        and the message contains images.
        """
        ctx_id = event.get("ctx_id", "?")
        segments = parse_segments(event.get("message", []))
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
        from yuubot.core.models import segments_to_json

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
        )

        if is_continuation:
            text = f"""{msg_xml}
{memory_hints}"""
        else:
            text = f"""你收到了来自{location}的消息。
{msg_xml}
{memory_hints}
请根据消息内容进行回复。回复时使用 im send 命令发送到 ctx {ctx_id}。
如果本次对话中获得了值得记住的新信息，请用 mem save 保存。
"""

        # Vision: if the model supports vision and message has images,
        # build multimodal content items
        if not self._has_vision(agent_name):
            return text

        from yuubot.core.models import ImageSegment
        image_segments = [s for s in segments if isinstance(s, ImageSegment)]
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

    def _get_bootstrap(self, agent_name: str) -> str:
        """Get bootstrap file path for the given agent, or empty string."""
        agents = self.config.yuuagents.get("agents", {})
        agent_cfg = agents.get(agent_name, {})
        return agent_cfg.get("bootstrap", "")

    async def run(
        self, match: MatchResult, event: dict, *,
        agent_name: str = "main",
        user_role: str = "",
        session: object | None = None,
    ) -> tuple[list, int, str]:
        """Passive mode: handle a command-triggered agent task.

        Returns (history, total_tokens, task_id) for session management.
        """
        await self._ensure_init()

        try:
            import yuutools as yt
            from yuuagents import Agent, tools
            from yuuagents.agent import AgentConfig, SimplePromptBuilder
            from yuuagents.loop import run as run_agent
            from yuuagents.context import AgentContext
        except ImportError:
            log.error("yuuagents not available, cannot run agent")
            return [], 0, ""

        ctx_id = event.get("ctx_id", 0)
        user_id = event.get("user_id", 0)
        is_continuation = session is not None and bool(getattr(session, "history", None))
        task_id = session.task_id if is_continuation and getattr(session, "task_id", "") else self._new_task_id()

        # Resolve group name for context
        group_name = ""
        if event.get("message_type") == "group":
            group_name = await self._resolve_group_name(event.get("group_id", 0))

        # Tool manager
        tool_names = self._resolve_tool_names(agent_name)
        if self._has_vision(agent_name) and "view_image" not in tool_names:
            tool_names.append("view_image")
        tool_manager = yt.ToolManager()
        for tool in tools.get(tool_names):
            tool_manager.register(tool)

        # Prompt
        persona = self._get_persona(agent_name)
        prompt_builder = SimplePromptBuilder()
        prompt_builder.add_section(persona)
        subagents_prompt = self._build_subagents_prompt(agent_name)
        if subagents_prompt:
            prompt_builder.add_section(subagents_prompt)
        needs_docker = self._agent_needs_docker(agent_name)
        if needs_docker and self._docker is not None:
            from yuuagents.daemon.docker import DOCKER_SYSTEM_PROMPT
            if DOCKER_SYSTEM_PROMPT:
                prompt_builder.add_section(DOCKER_SYSTEM_PROMPT)
        skills_docs = self._load_skills_docs(agent_name)
        if skills_docs:
            prompt_builder.add_section(skills_docs)

        # Bootstrap prompt
        bootstrap_path = self._get_bootstrap(agent_name)
        if bootstrap_path:
            prompt_builder.add_section(
                f"<bootstrap>\n"
                f"你有一个工作手册文件: {bootstrap_path}\n"
                f"每次启动新会话时请先用 read_file 阅读它，了解已有的工作约定。\n"
                f"完成任务后，如果有新的工作约定值得记录（如常用路径、操作习惯、项目结构），"
                f"请用 write_file 更新这个文件。保持文件简洁，不超过 50 行。\n"
                f"</bootstrap>"
            )

        agent_id = f"yuubot-{agent_name}-{ctx_id}"
        self._agent_name_map[agent_id] = agent_name

        config = AgentConfig(
            task_id=task_id,
            agent_id=agent_id,
            persona=persona,
            tools=tool_manager,
            llm=self._make_llm(agent_name),
            prompt_builder=prompt_builder,
            max_steps=self._get_max_steps(agent_name),
        )

        agent = Agent(config=config)
        task = await self._build_task(
            match, event, group_name=group_name,
            is_continuation=is_continuation,
            agent_name=agent_name,
        )

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
        context = AgentContext(
            task_id=task_id,
            agent_id=agent_id,
            workdir=workdir,
            docker_container=container_id,
            docker=self._docker if needs_docker else None,
            manager=self,
            session_manager=self._session_bridge,
            cli_guard=self._cli_guard,
        )

        with env.task_env(
            task_id=task_id, ctx_id=ctx_id,
            user_id=user_id, user_role=user_role,
            agent_name=agent_name,
        ):
            try:
                if is_continuation or is_multimodal:
                    await run_agent(agent, task=task_str, ctx=context, resume=True)
                else:
                    await run_agent(agent, task=task_str, ctx=context)
            except Exception:
                log.exception("Agent execution failed for ctx %s", ctx_id)

        return list(agent.history), agent.total_tokens, task_id

    async def run_scheduled(self, task: str, ctx_id: int | None, *, agent_name: str = "main") -> None:
        """Active mode: run a scheduled agent task."""
        await self._ensure_init()

        try:
            import yuutools as yt
            from yuuagents import Agent, tools
            from yuuagents.agent import AgentConfig, SimplePromptBuilder
            from yuuagents.loop import run as run_agent
            from yuuagents.context import AgentContext
        except ImportError:
            log.error("yuuagents not available")
            return

        task_id = self._new_task_id()

        tool_names = self._resolve_tool_names(agent_name)
        tool_manager = yt.ToolManager()
        for tool in tools.get(tool_names):
            tool_manager.register(tool)

        persona = self._get_persona(agent_name)
        prompt_builder = SimplePromptBuilder()
        prompt_builder.add_section(persona)
        subagents_prompt = self._build_subagents_prompt(agent_name)
        if subagents_prompt:
            prompt_builder.add_section(subagents_prompt)
        needs_docker = self._agent_needs_docker(agent_name)
        if needs_docker and self._docker is not None:
            from yuuagents.daemon.docker import DOCKER_SYSTEM_PROMPT
            if DOCKER_SYSTEM_PROMPT:
                prompt_builder.add_section(DOCKER_SYSTEM_PROMPT)
        skills_docs = self._load_skills_docs(agent_name)
        if skills_docs:
            prompt_builder.add_section(skills_docs)

        ctx_str = f"ctx {ctx_id}" if ctx_id else "无指定 ctx"
        full_task = f"""定时任务触发。
任务: {task}
目标: {ctx_str}

如需发送消息，使用 `ybot im send '<msg_json>' --ctx <ctx_id>`。
"""

        agent_id = f"yuubot-cron-{agent_name}-{ctx_id or 'global'}"
        self._agent_name_map[agent_id] = agent_name

        config = AgentConfig(
            task_id=task_id,
            agent_id=agent_id,
            persona=persona,
            tools=tool_manager,
            llm=self._make_llm(agent_name),
            prompt_builder=prompt_builder,
            max_steps=self._get_max_steps(agent_name),
        )

        agent = Agent(config=config)
        if needs_docker:
            workdir, container_id = await self._resolve_docker(task_id)
        else:
            from pathlib import Path
            workdir, container_id = str(Path.home()), ""
        context = AgentContext(
            task_id=task_id,
            agent_id=agent_id,
            workdir=workdir,
            docker_container=container_id,
            docker=self._docker if needs_docker else None,
            manager=self,
            cli_guard=self._cli_guard,
        )

        with env.task_env(task_id=task_id, ctx_id=ctx_id or "", agent_name=agent_name):
            try:
                await run_agent(agent, task=full_task, ctx=context)
            except Exception:
                log.exception("Scheduled agent failed: %s", task)
