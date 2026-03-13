"""Agent runner — orchestrate builder, runtime, and active runs."""

from __future__ import annotations

from typing import Any

import yuullm
from loguru import logger
from yuuagents import Agent, resume_agent, start_agent
from yuuagents.agent import AgentConfig, AgentStatus
from yuuagents.context import AgentContext, DelegateDepthExceededError
from yuuagents.flow import FlowKind, FlowManager, FlowStatus

from yuubot.characters import CHARACTER_REGISTRY
from yuubot.commands.tree import MatchResult
from yuubot.config import Config
from yuubot.core import env
from yuubot.core.types import InboundMessage
from yuubot.daemon.bot_info import BotInfo
from yuubot.daemon.builder import ActiveRun, AgentRunBuilder, SubprocessEnv, TaskBundle, TurnContext
from yuubot.daemon.llm_factory import make_compressor, make_summary_llm
from yuubot.daemon.runtime import AgentRuntime


class AgentRunner:
    """Create agent turns, launch them, and expose a small public API."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.runtime = AgentRuntime(config)
        self.bot_info = BotInfo(config=config)
        self._active_runs_by_runtime: dict[str, ActiveRun] = {}
        self._active_runs_by_ctx: dict[int, ActiveRun] = {}

    @property
    def _builder(self) -> AgentRunBuilder:
        return AgentRunBuilder(
            config=self.config,
            bot_info=self.bot_info,
            build_prompt=self.runtime.build_prompt,
            build_tool_manager=self.runtime.build_tool_manager,
            build_subprocess_env=self.runtime.build_subprocess_env,
            build_capability_context=self.runtime.build_capability_context,
            resolve_docker=self.runtime.resolve_docker,
            docker_home_info=self.runtime.docker_home_info,
            needs_docker=self.runtime.needs_docker,
            has_vision=self.runtime.has_vision,
            docker=self.runtime.docker,
        )

    async def _ensure_init(self) -> None:
        await self.runtime.ensure_init()

    async def stop(self) -> None:
        await self.runtime.stop()

    def _make_llm(self, agent_name: str):
        """Compatibility shim for tests and older call sites."""
        return self.runtime.make_llm(agent_name)

    def get_active_flow(self, ctx_id: int) -> object | None:
        active = self._active_runs_by_ctx.get(ctx_id)
        return None if active is None else active.flow

    def cancel_ctx(self, ctx_id: int) -> bool:
        active = self._active_runs_by_ctx.pop(ctx_id, None)
        if active is None or active.flow is None:
            return False
        self._active_runs_by_runtime.pop(active.runtime_id, None)
        if active.flow.task is not None and not active.flow.task.done():
            active.flow.task.cancel()
        active.flow.status = FlowStatus.CANCELLED
        logger.info("Flow cancelled for ctx={}", ctx_id)
        return True

    async def summarize(self, history: list, agent_name: str = "main") -> str:
        from yuubot.daemon.summarizer import summarize as summarize_history

        llm = make_summary_llm(self.config)
        return await summarize_history(history, llm)

    async def curate(self, history: list, ctx_id: int, user_id: int) -> None:
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
        base_env = SubprocessEnv(
            self.runtime.build_subprocess_env(
                task_id="",
                ctx_id=ctx_id,
                user_id=user_id,
                user_role="MASTER",
            )
        )
        turn = self._builder.build_delegated_turn(
            agent_name=agent_name,
            first_user_message=task,
            parent_env=base_env,
        )
        try:
            await self._run_simple_turn(turn)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)

    @staticmethod
    def _last_assistant_text(agent: Any) -> str:
        for msg in reversed(agent.history):
            role: str | None = None
            items: list[Any] | None = None
            if isinstance(msg, tuple) and len(msg) == 2:
                role, items = msg
            if role != "assistant" or not isinstance(items, list):
                continue
            text = "".join(item for item in items if isinstance(item, str)).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _build_continuation(session_history: list, bundle: TaskBundle) -> tuple[list, str]:
        """Merge or append new user message into session history.

        Returns (new_history, trigger) where trigger is what gets logged to the trace.
        Merges into the last user message when both sides are plain text (avoids
        consecutive user turns that confuse some LLMs).
        """
        history = list(session_history)
        if (
            history
            and isinstance(history[-1], tuple)
            and history[-1][0] == "user"
            and len(history[-1]) == 2
            and isinstance(history[-1][1], list)
            and not bundle.is_multimodal
            and len(bundle.user_items) == 1
            and isinstance(bundle.user_items[0], str)
            and history[-1][1]
            and isinstance(history[-1][1][0], str)
        ):
            merged = f"{history[-1][1][0]}\n\n{bundle.user_items[0]}"
            history[-1] = ("user", [merged])
            return history, merged
        else:
            history.append(("user", bundle.user_items))
            trigger = bundle.user_items[0] if bundle.user_items and isinstance(bundle.user_items[0], str) else bundle.task_text
            return history, trigger

    def _register_active_run(
        self,
        *,
        runtime_id: str,
        agent_name: str,
        subprocess_env: SubprocessEnv,
        flow: object | None,
        ctx_id: int | None,
    ) -> None:
        active = ActiveRun(
            runtime_id=runtime_id,
            agent_name=agent_name,
            subprocess_env=subprocess_env,
            flow=flow,
        )
        self._active_runs_by_runtime[runtime_id] = active
        if ctx_id is not None:
            self._active_runs_by_ctx[ctx_id] = active

    def _unregister_active_run(self, runtime_id: str, ctx_id: int | None) -> None:
        self._active_runs_by_runtime.pop(runtime_id, None)
        if ctx_id is None:
            return
        active = self._active_runs_by_ctx.get(ctx_id)
        if active is not None and active.runtime_id == runtime_id:
            self._active_runs_by_ctx.pop(ctx_id, None)

    async def _launch(
        self,
        *,
        turn: TurnContext,
        task_id: str,
        runtime_id: str,
        tool_names: list[str] | None = None,
        session_history: list | None = None,
        delegate_depth: int = 0,
        output_buffer: object | None = None,
        track_ctx: bool = False,
    ) -> Any:
        bundle = await self._builder.build_task_bundle(turn)
        run_ctx = await self._builder.build_run_context(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
            output_buffer=output_buffer,
        )
        agent = Agent(
            config=AgentConfig(
                task_id=task_id,
                agent_id=runtime_id,
                persona=run_ctx.persona,
                tools=run_ctx.tool_manager,
                llm=self._make_llm(turn.agent_name),
                prompt_builder=run_ctx.prompt_builder,
                max_steps=run_ctx.prompt_spec.agent_spec.max_steps,
                soft_timeout=run_ctx.prompt_spec.agent_spec.soft_timeout,
                silence_timeout=run_ctx.prompt_spec.agent_spec.silence_timeout,
                compressor=make_compressor(turn.agent_name, self.config),
            )
        )
        context = AgentContext(
            task_id=task_id,
            agent_id=runtime_id,
            workdir=run_ctx.docker_binding.workdir,
            docker_container=run_ctx.docker_binding.container_id,
            delegate_depth=delegate_depth,
            manager=self,
            docker=run_ctx.docker,
            skill_paths=self.config.skill_paths,
            subprocess_env=run_ctx.subprocess_env.values,
            current_output_buffer=output_buffer,
            output_buffer=output_buffer,
            addon_context=run_ctx.addon_context,
        )
        flow_manager = None
        root_flow = None
        ctx_id = turn.message.ctx_id if track_ctx else None
        if track_ctx:
            flow_manager = FlowManager()
            root_flow = flow_manager.create(FlowKind.AGENT, name=runtime_id)
        self._register_active_run(
            runtime_id=runtime_id,
            agent_name=turn.agent_name,
            subprocess_env=run_ctx.subprocess_env,
            flow=root_flow,
            ctx_id=ctx_id,
        )
        run_kwargs: dict[str, Any] = {"ctx": context}
        if flow_manager is not None and root_flow is not None:
            run_kwargs["flow_manager"] = flow_manager
            run_kwargs["root_flow"] = root_flow
        try:
            if turn.is_continuation and session_history is not None:
                # Continuation: merge or append new message, then resume from
                # the computed history. Caller owns all history content.
                history, trigger = self._build_continuation(session_history, bundle)
                agent.state.history = history
                agent.state.task = bundle.task_text
                agent.state.status = AgentStatus.RUNNING
                await resume_agent(agent, trigger, **run_kwargs)
            elif bundle.is_multimodal:
                # Fresh multimodal start: build history with image items directly.
                agent.state.history = [
                    yuullm.system(agent.full_system_prompt),
                    ("user", bundle.user_items),
                ]
                agent.state.task = bundle.task_text
                agent.state.status = AgentStatus.RUNNING
                await resume_agent(agent, bundle.task_text, **run_kwargs)
            else:
                await start_agent(agent, bundle.task_text, **run_kwargs)
        finally:
            self._unregister_active_run(runtime_id, ctx_id)
        return agent

    async def _run_simple_turn(
        self,
        turn: TurnContext,
        *,
        tool_names: list[str] | None = None,
        delegate_depth: int = 0,
        output_buffer: object | None = None,
    ) -> str:
        task_id = turn.task_id or self.runtime.new_task_id()
        ctx_id = turn.message.ctx_id
        runtime_id = f"agent-{turn.agent_name}-{ctx_id}" if ctx_id else f"agent-{turn.agent_name}-{task_id[:8]}"
        agent = await self._launch(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
            output_buffer=output_buffer,
        )
        return self._last_assistant_text(agent)

    async def _run_agent(
        self,
        agent_name: str,
        task: str,
        *,
        subprocess_env: dict[str, str],
        tool_names: list[str] | None = None,
        delegate_depth: int = 0,
        output_buffer: object | None = None,
    ) -> str:
        if agent_name not in CHARACTER_REGISTRY:
            raise ValueError(f"Unknown agent {agent_name!r}")
        run_env = dict(subprocess_env)
        run_env[env.AGENT_NAME] = agent_name
        turn = self._builder.build_delegated_turn(
            agent_name=agent_name,
            first_user_message=task,
            parent_env=SubprocessEnv(run_env),
        )
        return await self._run_simple_turn(
            turn,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
            output_buffer=output_buffer,
        )

    async def delegate(
        self,
        *,
        caller_agent: str,
        agent: str,
        first_user_message: str,
        tools: list[str] | None,
        delegate_depth: int,
        output_buffer: object | None = None,
    ) -> str:
        if delegate_depth > 3:
            raise DelegateDepthExceededError(
                max_depth=3,
                current_depth=delegate_depth,
                target_agent=agent,
            )
        parent = self._active_runs_by_runtime.get(caller_agent)
        caller_name = caller_agent if parent is None else parent.agent_name
        allowed = set()
        caller_char = CHARACTER_REGISTRY.get(caller_name)
        if caller_char is not None:
            allowed.update(caller_char.spec.subagents)
        if agent not in allowed:
            raise ValueError(f"Agent {caller_name!r} is not allowed to delegate to {agent!r}")
        parent_env = {} if parent is None else dict(parent.subprocess_env.values)
        return await self._run_agent(
            agent,
            first_user_message,
            subprocess_env=parent_env,
            tool_names=tools,
            delegate_depth=delegate_depth,
            output_buffer=output_buffer,
        )

    async def run(
        self,
        match: MatchResult,
        event: dict,
        *,
        agent_name: str = "main",
        user_role: str = "",
        session: object | None = None,
        pending_messages: list[InboundMessage] | None = None,
        handoff_text: str = "",
    ) -> tuple[list, int, str]:
        await self._ensure_init()
        session_history = getattr(session, "history", None)
        task_id = (
            getattr(session, "task_id", "")
            if session_history and getattr(session, "task_id", "")
            else self.runtime.new_task_id()
        )
        turn = await self._builder.build_turn_context(
            event=event,
            agent_name=agent_name,
            user_role=user_role,
            text_override=match.remaining,
            handoff_text=handoff_text,
            is_continuation=bool(session_history),
            pending_messages=pending_messages,
            task_id=task_id,
        )
        try:
            agent = await self._launch(
                turn=turn,
                task_id=task_id,
                runtime_id=f"yuubot-{agent_name}-{turn.message.ctx_id}",
                session_history=list(session_history) if session_history else None,
                track_ctx=True,
            )
        except BaseException:
            logger.exception("agent failed: ctx={} agent={} task_id={}", turn.message.ctx_id, agent_name, task_id)
            return [], 0, task_id
        return list(agent.history), agent.total_tokens, task_id

    async def run_scheduled(
        self,
        task: str,
        ctx_id: int | None,
        *,
        agent_name: str = "main",
    ) -> None:
        await self._ensure_init()
        task_id = self.runtime.new_task_id()
        ctx_str = f"ctx {ctx_id}" if ctx_id else "无指定 ctx"
        full_task = (
            "定时任务触发。\n"
            f"任务: {task}\n"
            f"目标: {ctx_str}\n\n"
            "如需发送消息，使用 im send 命令发送到对应 ctx。\n"
        )
        base_env = SubprocessEnv(
            self.runtime.build_subprocess_env(
                task_id=task_id,
                ctx_id=ctx_id or "",
                agent_name=agent_name,
            )
        )
        turn = self._builder.build_delegated_turn(
            agent_name=agent_name,
            first_user_message=full_task,
            parent_env=base_env,
        )
        try:
            await self._launch(
                turn=turn,
                task_id=task_id,
                runtime_id=f"yuubot-cron-{agent_name}-{ctx_id}" if ctx_id else f"yuubot-cron-{agent_name}-{task_id[:8]}",
            )
        except BaseException:
            logger.exception("Scheduled agent failed: {}", task)
