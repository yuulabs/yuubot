"""Builder pipeline for one agent turn.

The pipeline keeps transport, conversation state, rendering, and runtime
resources separate:

1. TurnContext collects the structured business input for one turn.
2. TaskBundle renders that input into the final LLM payload.
3. RunContext gathers the runtime resources needed to launch the agent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from yuuagents.context import DockerExecutor

import attrs
import yuullm
from yuuagents import ConversationInput, HandoffInput, ScheduledInput
from yuuagents.agent import AgentConfig
from yuuagents.capabilities import AgentCapabilities, DockerCapability
from yuuagents.context import AgentContext
from yuuagents.core.flow import Agent
from yuuagents.input import AgentInput, render_message_text

from yuubot.config import Config
from yuubot.core import env
from yuubot.core.media_paths import MediaPathContext, host_to_runtime, to_file_uri
from yuubot.core.models import AtSegment, ImageSegment
from yuubot.core.types import InboundMessage
from yuubot.daemon.bot_info import BotInfo
from yuubot.daemon.render import RenderContext, RenderPolicy, render_memory_hints, render_task
from yuubot.capabilities.im.formatter import format_segments
from yuubot.capabilities.im.query import last_message_row_id_by_user, recent_window_messages


@attrs.define(frozen=True)
class AgentEnv:
    values: dict[str, str]


@attrs.define(frozen=True)
class DockerBinding:
    workdir: str
    container_id: str = ""
    host_mount: str = ""
    host_home_dir: str = ""
    container_home_dir: str = ""


@attrs.define(frozen=True)
class TurnContext:
    message: InboundMessage
    agent_name: str
    user_role: str = ""
    text_override: str = ""
    handoff_text: str = ""
    startup_kind: str = "conversation"
    is_continuation: bool = False
    recent_ctx_upto_row_id: int = 0
    group_name: str = ""
    bot_name: str = ""
    task_id: str = ""


@attrs.define(frozen=True)
class TaskBundle:
    task_text: str
    user_items: list[Any]
    startup_input: AgentInput
    is_multimodal: bool = False


@attrs.define(frozen=True)
class RunContext:
    task_id: str
    runtime_id: str
    agent_name: str
    prompt_spec: Any
    system_prompt: str
    tool_names: list[str]
    tool_manager: Any
    persona: str
    agent_env: AgentEnv
    capability_context: Any
    docker: DockerExecutor | None
    docker_binding: DockerBinding
    delegate_depth: int = 0

    def build_agent_context(
        self,
        *,
        basin: Any,
        spawn_agent: Any,
    ) -> AgentContext:
        docker_capability = None
        if self.docker is not None and self.docker_binding.container_id:
            docker_capability = DockerCapability(
                executor=self.docker,
                container_id=self.docker_binding.container_id,
            )
        return AgentContext(
            task_id=self.task_id,
            agent_id=self.runtime_id,
            workdir=self.docker_binding.workdir,
            capabilities=AgentCapabilities(
                docker=docker_capability,
                basin=basin,
                spawn_agent=spawn_agent,
                subprocess_env=dict(self.agent_env.values),
            ),
            delegate_depth=self.delegate_depth,
        )

    def build_agent_config(self, *, llm: Any) -> AgentConfig:
        return AgentConfig(
            agent_id=self.runtime_id,
            system=self.system_prompt,
            tools=self.tool_manager,
            llm=llm,
            tool_batch_timeout=self.prompt_spec.agent_spec.tool_batch_timeout,
        )


@attrs.define(frozen=True)
class ActiveRun:
    runtime_id: str
    agent_name: str
    agent_env: AgentEnv


@attrs.define(frozen=True)
class AgentLaunch:
    config: AgentConfig
    context: AgentContext
    capability_context: Any

    @classmethod
    def from_run_context(
        cls,
        run_ctx: RunContext,
        *,
        llm: Any,
        basin: Any,
        spawn_agent: Any,
    ) -> AgentLaunch:
        return cls(
            config=run_ctx.build_agent_config(llm=llm),
            context=run_ctx.build_agent_context(
                basin=basin,
                spawn_agent=spawn_agent,
            ),
            capability_context=run_ctx.capability_context,
        )

    def open(
        self,
        startup_input: AgentInput,
        *,
        flow_id: str | None = None,
        initial_messages: list[yuullm.Message] | None = None,
        conversation_id: UUID | None = None,
    ) -> Agent:
        from yuubot.capabilities.runtime import register_capability_context

        if self.capability_context is not None:
            register_capability_context(self.context.agent_id, self.capability_context)

        agent = Agent(
            config=self.config,
            ctx=self.context,
            flow_id=flow_id,
            conversation_id=conversation_id,
            initial_messages=list(initial_messages or []),
        )
        agent.start(startup_input)
        return agent


@attrs.define
class AgentRunBuilder:
    config: Config
    bot_info: BotInfo
    build_prompt: Callable[[str], tuple[Any, Any]]
    build_tool_manager: Callable[[list[str]], Any]
    build_agent_env: Callable[..., dict[str, str]]
    build_capability_context: Callable[..., Any]
    resolve_docker: Callable[[str], Awaitable[tuple[str, str]]]
    docker_home_info: Callable[[str], Awaitable[tuple[str, str, str]]]
    needs_docker: Callable[[list[str]], bool]
    has_vision: Callable[[str], bool]
    docker: DockerExecutor | None = None

    async def build_turn_context(
        self,
        *,
        message: InboundMessage,
        agent_name: str,
        user_role: str = "",
        text_override: str = "",
        handoff_text: str = "",
        startup_kind: str = "conversation",
        is_continuation: bool = False,
        recent_ctx_upto_row_id: int = 0,
        task_id: str = "",
    ) -> TurnContext:
        group_name = ""
        if message.chat_type == "group":
            group_name = await self.bot_info.group_name(message.group_id)
        bot_name = await self.bot_info.bot_name()
        return TurnContext(
            message=message,
            agent_name=agent_name,
            user_role=user_role,
            text_override=text_override,
            handoff_text=handoff_text,
            startup_kind=startup_kind,
            is_continuation=is_continuation,
            recent_ctx_upto_row_id=recent_ctx_upto_row_id,
            group_name=group_name,
            bot_name=bot_name,
            task_id=task_id,
        )

    async def build_task_bundle(self, turn: TurnContext) -> TaskBundle:
        prompt_spec, _ = await self.build_prompt(turn.agent_name)
        tool_names = list(getattr(prompt_spec, "tools", []) or [])
        docker_binding = await self._build_docker_binding(
            turn.task_id,
            tool_names,
        )
        has_vision = await self.has_vision(turn.agent_name)
        memory_hints = await render_memory_hints(
            await self._memory_probe_text(turn),
            turn.message.ctx_id or None,
            skip_topic=turn.is_continuation,
        )
        text = await render_task(
            turn.message,
            RenderPolicy(),
            RenderContext(
                group_name=turn.group_name,
                bot_name=turn.bot_name,
                has_vision=has_vision,
                bot_qq=str(self.config.bot.qq),
                docker_host_mount=docker_binding.host_mount,
            ),
            is_continuation=turn.is_continuation,
            memory_hints=memory_hints,
            recent_ctx_upto_row_id=turn.recent_ctx_upto_row_id,
        )
        if not has_vision:
            user_items = [text]
            return TaskBundle(
                task_text=text,
                user_items=user_items,
                startup_input=self._build_startup_input(turn, user_items),
                is_multimodal=False,
            )

        items = self._build_multimodal_items(text, turn, docker_binding=docker_binding)
        if len(items) == 1:
            user_items = [text]
            return TaskBundle(
                task_text=text,
                user_items=user_items,
                startup_input=self._build_startup_input(turn, user_items),
                is_multimodal=False,
            )
        return TaskBundle(
            task_text=text,
            user_items=items,
            startup_input=self._build_startup_input(turn, items),
            is_multimodal=True,
        )

    async def build_run_context(
        self,
        *,
        turn: TurnContext,
        task_id: str,
        runtime_id: str,
        tool_names: list[str] | None = None,
        delegate_depth: int = 0,
    ) -> RunContext:
        prompt_spec, system_prompt = await self.build_prompt(turn.agent_name)
        resolved_tool_names = list(prompt_spec.tools) if tool_names is None else list(tool_names)
        tool_manager = self.build_tool_manager(resolved_tool_names)
        docker_binding = await self._build_docker_binding(task_id, resolved_tool_names)
        agent_env = AgentEnv(
            self.build_agent_env(
                task_id=task_id,
                ctx_id=turn.message.ctx_id,
                user_id=turn.message.sender.user_id,
                user_role=turn.user_role,
                agent_name=turn.agent_name,
                docker_mount=docker_binding.host_mount,
                docker_home=docker_binding.host_home_dir,
                docker_home_dir=docker_binding.container_home_dir,
            )
        )
        caps = prompt_spec.agent_spec.caps
        allowed_caps = None if "*" in caps else frozenset(caps)
        from yuubot.prompt import resolve_cap_visibility

        action_filters = resolve_cap_visibility(prompt_spec.agent_spec) or None
        capability_context = self.build_capability_context(
            ctx_id=turn.message.ctx_id,
            user_id=turn.message.sender.user_id,
            user_role=turn.user_role,
            agent_name=turn.agent_name,
            task_id=task_id,
            bot_name=turn.bot_name,
            allowed_caps=allowed_caps,
            action_filters=action_filters,
            docker_host_mount=docker_binding.host_mount,
            docker_home_host_dir=docker_binding.host_home_dir,
            docker_home_dir=docker_binding.container_home_dir,
        )
        capability_context.runtime_id = runtime_id
        persona = prompt_spec.resolved_sections[0][1] if prompt_spec.resolved_sections else ""
        return RunContext(
            task_id=task_id,
            runtime_id=runtime_id,
            agent_name=turn.agent_name,
            prompt_spec=prompt_spec,
            system_prompt=system_prompt,
            tool_names=resolved_tool_names,
            tool_manager=tool_manager,
            persona=persona,
            agent_env=agent_env,
            capability_context=capability_context,
            docker=self.docker if self.needs_docker(resolved_tool_names) else None,
            docker_binding=docker_binding,
            delegate_depth=delegate_depth,
        )

    def build_delegated_turn(
        self,
        *,
        agent_name: str,
        input: AgentInput,
        parent_env: AgentEnv,
    ) -> TurnContext:
        startup_kind = input.kind
        handoff_text = ""
        if isinstance(input, HandoffInput):
            handoff_text = self._render_input_messages(input.context)

        if isinstance(input, ConversationInput):
            primary_text = self._render_input_messages(input.messages)
        elif isinstance(input, HandoffInput):
            primary_text = self._render_input_messages(input.task)
        elif isinstance(input, ScheduledInput):
            primary_text = self._render_input_messages(input.trigger)
        else:
            primary_text = ""

        raw_text = primary_text or handoff_text or startup_kind
        event = {
            "post_type": "message",
            "message_type": "private",
            "message_id": 0,
            "user_id": int(parent_env.values.get(env.USER_ID, "0") or 0),
            "message": [{"type": "text", "data": {"text": raw_text}}],
            "raw_message": raw_text,
            "time": 0,
            "self_id": self.config.bot.qq,
            "sender": {"nickname": "", "card": ""},
            "ctx_id": int(parent_env.values.get(env.BOT_CTX, "0") or 0),
        }
        from yuubot.core.onebot import to_inbound_message

        return TurnContext(
            message=to_inbound_message(event),
            agent_name=agent_name,
            user_role=parent_env.values.get(env.USER_ROLE, ""),
            text_override=primary_text,
            handoff_text=handoff_text,
            startup_kind=startup_kind,
            bot_name="",
            task_id=parent_env.values.get(env.TASK_ID, ""),
        )

    async def _build_docker_binding(self, task_id: str, tool_names: list[str]) -> DockerBinding:
        if not self.needs_docker(tool_names):
            return DockerBinding(workdir=str(Path.home()))
        workdir, container_id = await self.resolve_docker(task_id)
        host_mount, host_home_dir, container_home_dir = await self.docker_home_info(container_id)
        return DockerBinding(
            workdir=workdir,
            container_id=container_id,
            host_mount=host_mount,
            host_home_dir=host_home_dir,
            container_home_dir=container_home_dir,
        )

    async def _memory_probe_text(self, turn: TurnContext) -> str:
        parts: list[str] = []
        if turn.handoff_text:
            parts.append(turn.handoff_text)
        if turn.text_override:
            parts.append(turn.text_override)
        elif (
            turn.is_continuation
            and turn.message.ctx_id
            and turn.recent_ctx_upto_row_id > 0
        ):
            anchor_row_id = await last_message_row_id_by_user(
                turn.message.ctx_id,
                user_id=self.config.bot.qq,
                before_row_id=turn.recent_ctx_upto_row_id,
            )
            messages, _ = await recent_window_messages(
                turn.message.ctx_id,
                after_row_id=anchor_row_id,
                upto_row_id=turn.recent_ctx_upto_row_id,
                limit=10,
            )
            if messages:
                parts.append("\n".join(
                    str(msg.get("content", "")).strip()
                    for msg in messages
                    if str(msg.get("content", "")).strip()
                ))
        else:
            parts.append(await format_segments(turn.message.segments))
        rendered_parts = [part for part in parts if part]
        return "\n".join(rendered_parts)

    def _build_startup_input(
        self,
        turn: TurnContext,
        user_items: list[Any],
    ) -> AgentInput:
        user_message = yuullm.user(*user_items)
        if turn.startup_kind == "scheduled":
            return ScheduledInput(trigger=[user_message])
        if turn.handoff_text:
            return HandoffInput(
                context=[yuullm.user(turn.handoff_text)],
                task=[user_message],
            )
        return ConversationInput(messages=[user_message])

    @staticmethod
    def _render_input_messages(messages: list[yuullm.Message]) -> str:
        parts = [render_message_text(message).strip() for message in messages]
        return "\n\n".join(part for part in parts if part)

    def _build_multimodal_items(
        self,
        text: str,
        turn: TurnContext,
        *,
        docker_binding: DockerBinding,
    ) -> list[object]:
        from yuullm import ImageItem, TextItem

        items: list[object] = [TextItem(type="text", text=text)]
        media_path_ctx = MediaPathContext.from_values(
            docker_host_mount=docker_binding.host_mount,
            host_home_dir=docker_binding.host_home_dir,
            container_home_dir=docker_binding.container_home_dir,
        )
        for seg in self._collect_image_segments(turn):
            url = ""
            if seg.local_path:
                runtime_path = host_to_runtime(seg.local_path, ctx=media_path_ctx)
                url = self._file_to_data_uri(runtime_path)
            elif seg.url:
                url = seg.url
            if url:
                items.append(ImageItem(type="image_url", image_url={"url": url}))
        return items

    def _collect_image_segments(self, turn: TurnContext) -> list[ImageSegment]:
        image_segments: list[ImageSegment] = []
        bot_qq = str(self.config.bot.qq)
        for segment in turn.message.segments:
            if isinstance(segment, AtSegment) and segment.qq == bot_qq:
                continue
            if isinstance(segment, ImageSegment):
                image_segments.append(segment)
        return image_segments

    @staticmethod
    def _file_to_data_uri(path: str) -> str:
        import base64

        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        target = Path(path)
        if not target.is_file():
            return to_file_uri(path)
        mime = mime_map.get(target.suffix.lower(), "application/octet-stream")
        data = base64.b64encode(target.read_bytes()).decode()
        return f"data:{mime};base64,{data}"
