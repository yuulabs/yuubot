"""Agent runner — host-driven step loop with signal support."""

from __future__ import annotations

import asyncio
import copy
import contextlib
from contextlib import aclosing

from loguru import logger
import yuullm
from yuuagents import Basin, ConversationInput, ScheduledInput
from yuuagents.context import DelegateDepthExceededError
from yuuagents.core.flow import FlowState
from yuuagents.input import AgentInput
from yuuagents.types import AgentStatus

from yuubot.capabilities.runtime import unregister_capability_context
from yuubot.characters import CHARACTER_REGISTRY
from yuubot.config import Config
from yuubot.core import env
from yuubot.core.types import InboundMessage
from yuubot.capabilities.im.formatter import format_messages_to_xml
from yuubot.capabilities.im.query import recent_messages
from yuubot.daemon.bot_info import BotInfo
from yuubot.daemon.builder import (
    ActiveRun,
    AgentEnv,
    AgentLaunch,
    AgentRunBuilder,
    RunContext,
    TurnContext,
)
from yuubot.daemon.conversation import Conversation
from yuubot.daemon.llm_factory import make_summary_llm
from yuubot.daemon.llm_trace import LLMTraceContext, wrap_llm_client
from yuubot.daemon.render import RenderContext, RenderPolicy, render_signal as _render_signal
from yuubot.daemon.runtime import AgentRuntime
from yuubot.daemon.runtime_session import RuntimeSession


class AgentRunner:
    """Create agent turns, launch them, and expose a small public API."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.runtime = AgentRuntime(config)
        self.bot_info = BotInfo(config=config)
        self._basin = Basin()
        self._active_runs_by_runtime: dict[str, ActiveRun] = {}
        self._active_runs_by_ctx: dict[int, ActiveRun] = {}
        self._signal_queues: dict[str, asyncio.Queue[str]] = {}
        self._silence_watchers: dict[str, asyncio.Task[None]] = {}
        self._step_loop_tasks: dict[str, asyncio.Task[str]] = {}
        self._running_sessions: dict[str, RuntimeSession] = {}

    @property
    def _builder(self) -> AgentRunBuilder:
        return AgentRunBuilder(
            config=self.config,
            bot_info=self.bot_info,
            build_prompt=self.runtime.build_prompt,
            build_tool_manager=self.runtime.build_tool_manager,
            build_agent_env=self.runtime.build_agent_env,
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
        for task in list(self._step_loop_tasks.values()):
            task.cancel()
        if self._step_loop_tasks:
            await asyncio.gather(*self._step_loop_tasks.values(), return_exceptions=True)

        for session in list(self._running_sessions.values()):
            session.cancel()
        waits = [
            session.wait()
            for session in self._running_sessions.values()
            if session.flow._task is not None
        ]
        if waits:
            await asyncio.gather(*waits, return_exceptions=True)

        for runtime_id in list(self._running_sessions):
            unregister_capability_context(runtime_id)
        self._running_sessions.clear()
        self._active_runs_by_runtime.clear()
        self._active_runs_by_ctx.clear()
        self._signal_queues.clear()
        self._silence_watchers.clear()
        self._step_loop_tasks.clear()
        self._prune_basin()
        await self.runtime.stop()

    async def _make_llm(self, agent_name: str):
        """Compatibility shim for tests and older call sites."""
        return await self.runtime.make_llm(agent_name)

    def get_active_run(self, ctx_id: int) -> ActiveRun | None:
        return self._active_runs_by_ctx.get(ctx_id)

    def cancel_ctx(self, ctx_id: int) -> bool:
        active = self._active_runs_by_ctx.get(ctx_id)
        if active is None:
            return False
        step_task = self._step_loop_tasks.get(active.runtime_id)
        if step_task is not None:
            step_task.cancel()
        session = self._running_sessions.get(active.runtime_id)
        if session is not None:
            session.cancel()
        logger.info("Run cancelled for ctx={}", ctx_id)
        return True

    # -- Signal support --

    def enqueue_signal(self, runtime_id: str, text: str) -> None:
        """Enqueue the latest signal message for a running agent."""
        q = self._signal_queues.get(runtime_id)
        if q is not None:
            while not q.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(text)

    async def render_signal(
        self,
        msg: InboundMessage,
        *,
        upto_row_id: int = 0,
    ) -> str:
        """Render a message through the full pipeline for use as a signal."""
        bot_name = await self.bot_info.bot_name()
        group_name = ""
        if msg.chat_type == "group" and msg.group_id:
            group_name = await self.bot_info.group_name(msg.group_id)
        docker_host_mount = ""
        active = self._active_runs_by_ctx.get(msg.ctx_id)
        if active is not None:
            docker_host_mount = active.agent_env.values.get(env.DOCKER_HOST_MOUNT, "")
        context = RenderContext(
            group_name=group_name,
            bot_name=bot_name,
            bot_qq=str(self.config.bot.qq),
            docker_host_mount=docker_host_mount,
        )
        return await _render_signal(
            msg,
            RenderPolicy(),
            context,
            upto_row_id=upto_row_id,
        )

    # -- Summarize / curate --

    async def summarize(
        self,
        runtime_session: object,
        history: list,
        agent_name: str = "main",
    ) -> str:
        from yuubot.daemon.summarizer import summarize as summarize_history
        from yuubot.daemon.summarizer import summarize_via_fork

        if isinstance(runtime_session, RuntimeSession):
            try:
                result = await summarize_via_fork(runtime_session)
                if result:
                    return result
                logger.warning("Fork summarizer returned empty, falling back")
            except Exception:
                logger.exception("Fork summarizer failed, falling back")

        llm = await make_summary_llm(self.config)
        return await summarize_history(history, llm)

    async def curate(self, conv: Conversation) -> None:
        agent_name = "mem_curator"
        if agent_name not in CHARACTER_REGISTRY:
            logger.debug("mem_curator not configured, skipping")
            return

        await self._ensure_init()

        from yuubot.daemon.summarizer import extract_original_task, render_for_curator

        history = conv.history
        ctx_id = conv.ctx_id
        user_id = conv.started_by

        bot_name = await self.bot_info.bot_name()
        qq_transcript = ""
        if conv.start_row_id > 0 and conv.latest_ctx_row_id >= conv.start_row_id:
            records = await recent_messages(
                ctx_id,
                after_row_id=conv.start_row_id - 1,
                upto_row_id=conv.latest_ctx_row_id,
                limit=None,
            )
            if records:
                qq_transcript = await format_messages_to_xml(
                    records,
                    bot_qq=self.config.bot.qq,
                    bot_name=bot_name,
                )

        task = (
            f"以下是本轮 session 的真实 QQ 消息记录与内部摘要，请整理记忆。\n\n"
            f"原始任务：\n{extract_original_task(history)}\n\n"
            f"真实 QQ 消息记录（若与内部摘要冲突，以这里和人类纠错为准）：\n"
            f"{qq_transcript or '（无可用 QQ 记录）'}\n\n"
            f"内部摘要（可能遗漏或误读，仅供参考）：\n{render_for_curator(history)}\n\n"
            "写记忆时，优先使用真实 QQ 记录里可见的稳定锚点来把事实写完整，例如 qq、明确名字、绝对时间、URL、img_id。\n"
            "不要把只靠当前对话语境才能理解的简称、代词、相对称呼、相对时间直接写进记忆；若能补全锚点后再保存，否则不要保存。\n\n"
            "如果完整写成绝对事实仍可能歧义，或上下文太长难以压缩，请把来源消息窗口直接写进记忆，格式优先用「来源: msg_id=<起始消息ID> +<后续条数>条」。\n"
            "只引用证明该事实所需的最小连续窗口，便于后续按 msg_id 回查来源。\n\n"
            f"ctx_id: {ctx_id}\n"
        )
        base_env = AgentEnv(
            self.runtime.build_agent_env(
                task_id="",
                ctx_id=ctx_id,
                user_id=user_id,
                user_role="MASTER",
            )
        )
        turn = self._builder.build_delegated_turn(
            agent_name=agent_name,
            input=ConversationInput(messages=[yuullm.user(task)]),
            parent_env=base_env,
        )
        try:
            await self._run_simple_turn(turn)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)

    @staticmethod
    def _last_assistant_text(session: RuntimeSession) -> str:
        for msg in reversed(session.history):
            role, items = msg
            if role != "assistant":
                continue
            text = "".join(
                str(item.get("text", ""))
                for item in items
                if item.get("type") == "text"
            ).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        if max_chars <= 32:
            return text[:max_chars]
        return text[: max_chars - 15].rstrip() + "\n...[truncated]"

    @staticmethod
    def _snapshot_history(history: list[yuullm.Message]) -> list[yuullm.Message]:
        return copy.deepcopy(history)

    @classmethod
    def _summarize_history_message(
        cls,
        message: yuullm.Message,
        *,
        max_text_chars: int = 96,
    ) -> str:
        role, items = message
        text_parts: list[str] = []
        tool_calls: list[str] = []
        other_types: list[str] = []

        for item in items:
            item_type = item.get("type", "")
            if item_type == "text":
                text = item.get("text", "")
                if isinstance(text, str) and text.strip():
                    text_parts.append(cls._truncate_text(text.strip(), max_text_chars))
                continue
            if item_type == "tool_call":
                name = item.get("name", "?")
                tool_calls.append(str(name))
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                text_parts.append(cls._truncate_text(content.strip(), max_text_chars))
                continue
            other_types.append(str(item_type or "?"))

        segments: list[str] = [role]
        if text_parts:
            segments.append(f'text="{ " | ".join(text_parts) }"')
        if tool_calls:
            segments.append(f"tool_calls={','.join(tool_calls)}")
        if other_types:
            segments.append(f"types={','.join(other_types)}")
        return "[" + " ".join(segments) + "]"

    @classmethod
    def _summarize_history_delta(
        cls,
        before: list[yuullm.Message],
        after: list[yuullm.Message],
        *,
        max_messages: int = 3,
    ) -> str:
        shared = min(len(before), len(after))
        first_diff = shared
        for idx in range(shared):
            if before[idx] != after[idx]:
                first_diff = idx
                break

        delta = after[first_diff:]
        if not delta:
            return "none"

        parts = [
            cls._summarize_history_message(message)
            for message in delta[:max_messages]
        ]
        if len(delta) > max_messages:
            parts.append(f"+{len(delta) - max_messages} more")
        return "; ".join(parts)

    @classmethod
    def _summarize_history_tail(
        cls,
        history: list[yuullm.Message],
        *,
        limit: int = 3,
    ) -> str:
        if not history:
            return "empty"
        tail = history[-limit:]
        parts = [cls._summarize_history_message(message) for message in tail]
        if len(history) > limit:
            parts.insert(0, f"... {len(history) - limit} earlier")
        return "; ".join(parts)

    @staticmethod
    def _format_usage(usage: yuullm.Usage | None) -> str:
        if usage is None:
            return "none"
        parts = [
            f"in={int(getattr(usage, 'input_tokens', 0) or 0)}",
            f"out={int(getattr(usage, 'output_tokens', 0) or 0)}",
            f"total={int(getattr(usage, 'total_tokens', 0) or 0)}",
        ]
        cache_read = int(getattr(usage, "cache_read_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_write_tokens", 0) or 0)
        if cache_read:
            parts.append(f"cache_read={cache_read}")
        if cache_write:
            parts.append(f"cache_write={cache_write}")
        return " ".join(parts)

    def _register_active_run(
        self,
        *,
        runtime_id: str,
        agent_name: str,
        agent_env: AgentEnv,
        ctx_id: int | None,
    ) -> None:
        active = ActiveRun(
            runtime_id=runtime_id,
            agent_name=agent_name,
            agent_env=agent_env,
        )
        self._active_runs_by_runtime[runtime_id] = active
        self._signal_queues[runtime_id] = asyncio.Queue()
        if ctx_id is not None:
            self._active_runs_by_ctx[ctx_id] = active

    def _unregister_active_run(self, runtime_id: str, ctx_id: int | None) -> None:
        self._active_runs_by_runtime.pop(runtime_id, None)
        self._signal_queues.pop(runtime_id, None)
        watcher = self._silence_watchers.pop(runtime_id, None)
        if watcher is not None:
            watcher.cancel()
        if ctx_id is None:
            return
        active = self._active_runs_by_ctx.get(ctx_id)
        if active is not None and active.runtime_id == runtime_id:
            self._active_runs_by_ctx.pop(ctx_id, None)

    @staticmethod
    def _history_has_im_send(session: RuntimeSession) -> bool:
        return session.has_tool_call("call_cap_cli", argument_contains="im send")

    async def _watch_silence_timeout(
        self,
        *,
        runtime_id: str,
        agent_name: str,
        session: RuntimeSession,
        ctx_id: int | None,
        timeout: float | None,
    ) -> None:
        if timeout is None or timeout <= 0:
            return

        try:
            await asyncio.sleep(timeout)
            active = self._active_runs_by_runtime.get(runtime_id)
            if active is None:
                return
            if self._history_has_im_send(session):
                return

            logger.info(
                "Silence timeout reached: ctx={} agent={} runtime_id={}",
                ctx_id,
                agent_name,
                runtime_id,
            )
            session.send(
                yuullm.user(
                    "你已经长时间没有使用 im send 向用户同步进展。"
                    "请立即停止等待中的前台工具结果，先用 im send 简短说明你正在做什么、"
                    "为什么还需要一些时间，以及下一步会怎么继续。",
                ),
                defer_tools=True,
            )
        except asyncio.CancelledError:
            pass

    def _prune_basin(self) -> None:
        for flow in list(self._basin.iter_flows()):
            task = getattr(flow, "_task", None)
            if flow.state in (
                FlowState.DONE,
                FlowState.ERROR,
                FlowState.CANCELLED,
            ) and (task is None or task.done()):
                self._basin.forget(flow.id)

    def _cleanup_runtime_session(self, session: RuntimeSession) -> None:
        self._running_sessions.pop(session.runtime_id, None)
        unregister_capability_context(session.runtime_id)
        self._prune_basin()

    async def _interrupt_session(self, session: RuntimeSession) -> None:
        session.cancel()
        waits = [
            child.wait()
            for child in session.flow.children
            if child._task is not None
        ]
        if waits:
            await asyncio.gather(*waits, return_exceptions=True)
        await session.kill()

    def _ensure_delegate_allowed(self, caller_name: str, target_agent: str) -> None:
        allowed = set()
        caller_char = CHARACTER_REGISTRY.get(caller_name)
        if caller_char is not None:
            allowed.update(caller_char.spec.subagents)
        if target_agent not in allowed:
            raise ValueError(
                f"Agent {caller_name!r} is not allowed to delegate to {target_agent!r}",
            )

    def _make_spawn_agent(self, parent_run_ctx: RunContext):
        async def _spawn(
            parent_flow,
            agent: str,
            input: AgentInput,
            tools: list[str] | None,
            delegate_depth: int,
        ):
            if delegate_depth > 3:
                raise DelegateDepthExceededError(
                    max_depth=3,
                    current_depth=delegate_depth - 1,
                    target_agent=agent,
                )

            self._ensure_delegate_allowed(parent_run_ctx.agent_name, agent)

            child_task_id = parent_run_ctx.task_id or self.runtime.new_task_id()
            child_flow_id = self.runtime.new_task_id()
            child_runtime_id = f"delegate-{agent}-{child_flow_id[:8]}"
            turn = self._builder.build_delegated_turn(
                agent_name=agent,
                input=input,
                parent_env=parent_run_ctx.agent_env,
            )
            session, _ = await self._open_runtime_session(
                turn=turn,
                task_id=child_task_id,
                runtime_id=child_runtime_id,
                flow_id=child_flow_id,
                tool_names=tools,
                delegate_depth=delegate_depth,
            )
            session.flow.parent = parent_flow
            if session.flow not in parent_flow.children:
                parent_flow.children.append(session.flow)
            session.flow.start(self._drive_background_session(session))
            return session.agent

        return _spawn

    async def _open_runtime_session(
        self,
        *,
        turn: TurnContext,
        task_id: str,
        runtime_id: str,
        flow_id: str,
        tool_names: list[str] | None = None,
        previous: RuntimeSession | None = None,
        delegate_depth: int = 0,
    ) -> tuple[RuntimeSession, RunContext]:
        bundle = await self._builder.build_task_bundle(turn)
        run_ctx = await self._builder.build_run_context(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
        )
        llm = wrap_llm_client(
            await self._make_llm(turn.agent_name),
            trace=LLMTraceContext(
                ctx_id=turn.message.ctx_id or None,
                runtime_id=runtime_id,
                task_id=task_id,
                agent_name=turn.agent_name,
            ),
        )
        launch = AgentLaunch.from_run_context(
            run_ctx,
            llm=llm,
            basin=self._basin,
            spawn_agent=self._make_spawn_agent(run_ctx),
        )

        initial_messages = previous.history if previous is not None else None
        conversation_id = previous.conversation_id if previous is not None else None
        try:
            agent = launch.open(
                bundle.startup_input,
                flow_id=flow_id,
                initial_messages=initial_messages,
                conversation_id=conversation_id,
            )
        except Exception:
            unregister_capability_context(runtime_id)
            raise

        session = RuntimeSession(
            task_id=task_id,
            runtime_id=runtime_id,
            agent_name=turn.agent_name,
            agent=agent,
            capability_context=run_ctx.capability_context,
        )
        session.flow.info["runtime_id"] = runtime_id
        session.flow.info["agent_name"] = turn.agent_name
        self._running_sessions[runtime_id] = session
        return session, run_ctx

    async def _drive_background_session(self, session: RuntimeSession) -> None:
        try:
            async with aclosing(session.agent.steps()) as gen:
                async for _ in gen:
                    pass
            session.status = AgentStatus.DONE
        except asyncio.CancelledError:
            session.status = AgentStatus.CANCELLED
            with contextlib.suppress(Exception):
                await self._interrupt_session(session)
            raise
        except Exception as exc:
            session.status = AgentStatus.ERROR
            session.flow.info["error"] = str(exc)
            with contextlib.suppress(Exception):
                await self._interrupt_session(session)
            logger.exception(
                "Delegated agent failed: runtime_id={} agent={}",
                session.runtime_id,
                session.agent_name,
            )
            raise
        finally:
            self._cleanup_runtime_session(session)

    async def _drive_foreground_session(
        self,
        *,
        session: RuntimeSession,
        run_ctx: RunContext,
        ctx_id: int | None,
    ) -> str:
        timeout = self.config.daemon.agent_timeout
        max_steps = run_ctx.prompt_spec.agent_spec.max_steps
        stop_reason = "natural"
        step_count = 0

        logger.info(
            "Step loop started: ctx={} runtime_id={} agent={} timeout={}s max_steps={}",
            ctx_id,
            session.runtime_id,
            session.agent_name,
            timeout,
            max_steps,
        )
        t0 = asyncio.get_running_loop().time()
        history_snapshot = self._snapshot_history(session.history)

        try:
            timeout_ctx = asyncio.timeout(timeout) if timeout else contextlib.nullcontext()
            async with timeout_ctx:
                async with aclosing(session.agent.steps()) as gen:
                    async for step in gen:
                        step_count += 1
                        current_history = session.history
                        history_delta = self._summarize_history_delta(
                            history_snapshot,
                            current_history,
                        )
                        logger.debug(
                            "Step loop event: ctx={} runtime_id={} step={} rounds={} done={} usage={} delta={}",
                            ctx_id,
                            session.runtime_id,
                            step_count,
                            getattr(step, "rounds", "?"),
                            step.done,
                            self._format_usage(session.last_usage),
                            history_delta,
                        )
                        history_snapshot = self._snapshot_history(current_history)
                        if step.done:
                            session.status = AgentStatus.DONE
                            session.flow.state = FlowState.DONE
                            logger.debug(
                                "Step loop done (natural): ctx={} runtime_id={} steps={} last_assistant={} history_tail={}",
                                ctx_id,
                                session.runtime_id,
                                step_count,
                                bool(self._last_assistant_text(session)),
                                self._summarize_history_tail(current_history),
                            )
                            return stop_reason
                        if max_steps and step.rounds >= max_steps:
                            stop_reason = "max_steps"
                            logger.info(
                                "Step loop done (max_steps): ctx={} runtime_id={} steps={} history_tail={}",
                                ctx_id,
                                session.runtime_id,
                                step_count,
                                self._summarize_history_tail(current_history),
                            )
                            await self._interrupt_session(session)
                            session.status = AgentStatus.CANCELLED
                            session.flow.state = FlowState.CANCELLED
                            return stop_reason
        except TimeoutError:
            stop_reason = "timeout"
            logger.warning(
                "Agent run timed out after {}s: ctx={} runtime_id={} steps={}",
                timeout,
                ctx_id,
                session.runtime_id,
                step_count,
            )
            await self._interrupt_session(session)
            session.status = AgentStatus.CANCELLED
            session.flow.state = FlowState.CANCELLED
            return stop_reason
        except asyncio.CancelledError:
            stop_reason = "cancelled"
            logger.info(
                "Agent run cancelled: ctx={} runtime_id={} steps={}",
                ctx_id,
                session.runtime_id,
                step_count,
            )
            with contextlib.suppress(Exception):
                await self._interrupt_session(session)
            session.status = AgentStatus.CANCELLED
            session.flow.state = FlowState.CANCELLED
            raise
        except Exception as exc:
            stop_reason = "error"
            session.status = AgentStatus.ERROR
            session.flow.state = FlowState.ERROR
            session.flow.info["error"] = str(exc)
            with contextlib.suppress(Exception):
                await self._interrupt_session(session)
            logger.exception(
                "Agent run failed: ctx={} runtime_id={}",
                ctx_id,
                session.runtime_id,
            )
            raise
        else:
            return stop_reason
        finally:
            elapsed = asyncio.get_running_loop().time() - t0
            final_history = session.history
            final_assistant = self._last_assistant_text(session)
            logger.info(
                "Step loop ended: ctx={} runtime_id={} reason={} steps={} elapsed={:.1f}s history_messages={} final_assistant={} usage={}",
                ctx_id,
                session.runtime_id,
                stop_reason,
                step_count,
                elapsed,
                len(final_history),
                bool(final_assistant),
                self._format_usage(session.last_usage),
            )
            if not final_assistant:
                logger.warning(
                    "Step loop ended without final assistant text: ctx={} runtime_id={} reason={} history_tail={}",
                    ctx_id,
                    session.runtime_id,
                    stop_reason,
                    self._summarize_history_tail(final_history),
                )

    async def _launch(
        self,
        *,
        turn: TurnContext,
        task_id: str,
        runtime_id: str,
        tool_names: list[str] | None = None,
        session: RuntimeSession | None = None,
        delegate_depth: int = 0,
        track_ctx: bool = False,
    ) -> RuntimeSession:
        working_session, run_ctx = await self._open_runtime_session(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            flow_id=task_id,
            tool_names=tool_names,
            previous=session,
            delegate_depth=delegate_depth,
        )
        ctx_id = turn.message.ctx_id if track_ctx else None

        self._register_active_run(
            runtime_id=runtime_id,
            agent_name=turn.agent_name,
            agent_env=run_ctx.agent_env,
            ctx_id=ctx_id,
        )
        silence_timeout = run_ctx.prompt_spec.agent_spec.silence_timeout
        self._silence_watchers[runtime_id] = asyncio.create_task(
            self._watch_silence_timeout(
                runtime_id=runtime_id,
                agent_name=turn.agent_name,
                session=working_session,
                ctx_id=ctx_id,
                timeout=silence_timeout,
            )
        )

        signal_task = asyncio.create_task(
            self._signal_drainer(working_session, runtime_id)
        )
        step_task = asyncio.create_task(
            self._drive_foreground_session(
                session=working_session,
                run_ctx=run_ctx,
                ctx_id=ctx_id,
            )
        )
        self._step_loop_tasks[runtime_id] = step_task

        try:
            stop_reason = await step_task
            working_session.stop_reason = stop_reason
            return working_session
        except asyncio.CancelledError:
            working_session.stop_reason = "cancelled"
            raise
        finally:
            signal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await signal_task
            self._step_loop_tasks.pop(runtime_id, None)
            self._unregister_active_run(runtime_id, ctx_id)
            self._cleanup_runtime_session(working_session)

    async def _signal_drainer(self, session: RuntimeSession, runtime_id: str) -> None:
        """Continuously drain signals and forward them to the running agent."""
        try:
            q = self._signal_queues.get(runtime_id)
            if q is None:
                return
            while True:
                msg = await q.get()
                session.send(yuullm.user(msg))
        except asyncio.CancelledError:
            pass

    async def _run_simple_turn(
        self,
        turn: TurnContext,
        *,
        tool_names: list[str] | None = None,
        delegate_depth: int = 0,
    ) -> str:
        task_id = turn.task_id or self.runtime.new_task_id()
        ctx_id = turn.message.ctx_id
        runtime_id = (
            f"agent-{turn.agent_name}-{ctx_id}"
            if ctx_id
            else f"agent-{turn.agent_name}-{task_id[:8]}"
        )
        session = await self._launch(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
        )
        return self._last_assistant_text(session)

    async def run_conversation(
        self,
        message: InboundMessage,
        *,
        agent_name: str = "main",
        user_role: str = "",
        session: object | None = None,
        handoff_text: str = "",
        text_override: str = "",
        recent_ctx_upto_row_id: int = 0,
    ) -> RuntimeSession | None:
        await self._ensure_init()
        current_session = getattr(session, "session", None)
        task_id = getattr(current_session, "task_id", "") or self.runtime.new_task_id()
        turn = await self._builder.build_turn_context(
            message=message,
            agent_name=agent_name,
            user_role=user_role,
            text_override=text_override,
            handoff_text=handoff_text,
            startup_kind="handoff" if handoff_text else "conversation",
            is_continuation=current_session is not None,
            recent_ctx_upto_row_id=recent_ctx_upto_row_id,
            task_id=task_id,
        )
        runtime_id = f"yuubot-{agent_name}-{turn.message.ctx_id}"
        try:
            runtime_session = await self._launch(
                turn=turn,
                task_id=task_id,
                runtime_id=runtime_id,
                session=current_session,
                track_ctx=True,
            )
        except asyncio.CancelledError:
            logger.info(
                "agent cancelled: ctx={} agent={} task_id={}",
                turn.message.ctx_id,
                agent_name,
                task_id,
            )
            return None
        except BaseException:
            logger.exception(
                "agent failed: ctx={} agent={} task_id={}",
                turn.message.ctx_id,
                agent_name,
                task_id,
            )
            return None
        return runtime_session

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
        base_env = AgentEnv(
            self.runtime.build_agent_env(
                task_id=task_id,
                ctx_id=ctx_id or "",
                agent_name=agent_name,
            )
        )
        turn = self._builder.build_delegated_turn(
            agent_name=agent_name,
            input=ScheduledInput(trigger=[yuullm.user(full_task)]),
            parent_env=base_env,
        )
        try:
            await self._launch(
                turn=turn,
                task_id=task_id,
                runtime_id=(
                    f"yuubot-cron-{agent_name}-{ctx_id}"
                    if ctx_id
                    else f"yuubot-cron-{agent_name}-{task_id[:8]}"
                ),
            )
        except BaseException:
            logger.exception("Scheduled agent failed: {}", task)
