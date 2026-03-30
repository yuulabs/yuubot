"""Agent runner — host-driven step loop with signal support."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from loguru import logger
import yuullm
from yuuagents import Session
from yuuagents.context import DelegateDepthExceededError
from yuuagents.types import AgentStatus

from yuubot.characters import CHARACTER_REGISTRY
from yuubot.config import Config
from yuubot.core import env
from yuubot.core.types import InboundMessage
from yuubot.daemon.bot_info import BotInfo
from yuubot.daemon.builder import ActiveRun, AgentEnv, AgentRunBuilder, SessionLaunch, TurnContext
from yuubot.daemon.render import RenderContext, RenderPolicy, render_signal as _render_signal
from yuubot.daemon.llm_factory import make_summary_llm
from yuubot.daemon.runtime import AgentRuntime


class AgentRunner:
    """Create agent turns, launch them, and expose a small public API."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.runtime = AgentRuntime(config)
        self.bot_info = BotInfo(config=config)
        self._active_runs_by_runtime: dict[str, ActiveRun] = {}
        self._active_runs_by_ctx: dict[int, ActiveRun] = {}
        self._signal_queues: dict[str, asyncio.Queue[str]] = {}
        self._silence_watchers: dict[str, asyncio.Task[None]] = {}
        self._delegate_sessions_by_run_id: dict[str, Session] = {}
        self._running_sessions: dict[str, Session] = {}

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
        await self.runtime.stop()

    def _make_llm(self, agent_name: str):
        """Compatibility shim for tests and older call sites."""
        return self.runtime.make_llm(agent_name)

    def get_active_run(self, ctx_id: int) -> ActiveRun | None:
        return self._active_runs_by_ctx.get(ctx_id)

    def cancel_ctx(self, ctx_id: int) -> bool:
        active = self._active_runs_by_ctx.pop(ctx_id, None)
        if active is None:
            return False
        self._active_runs_by_runtime.pop(active.runtime_id, None)
        session = self._running_sessions.get(active.runtime_id)
        if session is not None:
            session.cancel()
        logger.info("Run cancelled for ctx={}", ctx_id)
        return True

    def _cancel_session_step_loop(self, runtime_id: str) -> None:
        """Cancel the step-loop task driving a session, if any."""
        session = self._running_sessions.get(runtime_id)
        if session is not None:
            session.cancel()

    # -- Signal support --

    def enqueue_signal(self, runtime_id: str, text: str) -> None:
        """Enqueue a signal message for a running agent."""
        q = self._signal_queues.get(runtime_id)
        if q is not None:
            q.put_nowait(text)

    async def render_signal(self, msg: InboundMessage) -> str:
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
        return await _render_signal(msg, RenderPolicy(), context)

    # -- Summarize / curate --

    async def summarize(
        self,
        runtime_session: object,
        history: list,
        agent_name: str = "main",
    ) -> str:
        from yuuagents import Session as _Session
        from yuubot.daemon.summarizer import summarize as summarize_history
        from yuubot.daemon.summarizer import summarize_via_fork

        # Prefer fork-based summarization (cache-friendly, higher quality).
        # Fall back to the external summarizer on failure.
        if isinstance(runtime_session, _Session):
            try:
                result = await summarize_via_fork(runtime_session)
                if result:
                    return result
                logger.warning("Fork summarizer returned empty, falling back")
            except Exception:
                logger.exception("Fork summarizer failed, falling back")

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
            first_user_message=task,
            parent_env=base_env,
        )
        try:
            await self._run_simple_turn(turn)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)

    @staticmethod
    def _last_assistant_text(session: Session) -> str:
        for msg in reversed(session.history):
            role, items = msg
            if role != "assistant":
                continue
            text = "".join(
                item["text"] for item in items if item.get("type") == "text"
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
        self._running_sessions.pop(runtime_id, None)
        watcher = self._silence_watchers.pop(runtime_id, None)
        if watcher is not None:
            watcher.cancel()
        if ctx_id is None:
            return
        active = self._active_runs_by_ctx.get(ctx_id)
        if active is not None and active.runtime_id == runtime_id:
            self._active_runs_by_ctx.pop(ctx_id, None)

    @staticmethod
    def _history_has_im_send(session: Session) -> bool:
        return session.has_tool_call("call_cap_cli", argument_contains="im send")

    async def _watch_silence_timeout(
        self,
        *,
        runtime_id: str,
        agent_name: str,
        session: Session,
        ctx_id: int | None,
    ) -> None:
        timeout = session.config.silence_timeout
        if timeout is None or timeout <= 0:
            return

        try:
            await asyncio.sleep(timeout)
            active = self._active_runs_by_runtime.get(runtime_id)
            if active is None:
                return
            if self._history_has_im_send(session):
                return

            if session.agent is None:
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

    async def _launch(
        self,
        *,
        turn: TurnContext,
        task_id: str,
        runtime_id: str,
        tool_names: list[str] | None = None,
        session_history: list | None = None,
        session: Session | None = None,
        delegate_depth: int = 0,
        track_ctx: bool = False,
    ) -> Session:
        bundle = await self._builder.build_task_bundle(turn)
        run_ctx = await self._builder.build_run_context(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
        )
        launch = SessionLaunch.from_run_context(
            run_ctx,
            llm=self._make_llm(turn.agent_name),
            manager=self,
        )
        working_session = launch.open()
        ctx_id = turn.message.ctx_id if track_ctx else None

        self._register_active_run(
            runtime_id=runtime_id,
            agent_name=turn.agent_name,
            agent_env=run_ctx.agent_env,
            ctx_id=ctx_id,
        )
        self._silence_watchers[runtime_id] = asyncio.create_task(
            self._watch_silence_timeout(
                runtime_id=runtime_id,
                agent_name=turn.agent_name,
                session=working_session,
                ctx_id=ctx_id,
            )
        )

        # Start or resume the session
        if session is not None and hasattr(session, "history") and session.history:
            working_session.resume(
                bundle.startup_input,
                history=session.history,
                conversation_id=getattr(session, "conversation_id", None),
            )
        else:
            working_session.start(bundle.startup_input)

        self._running_sessions[runtime_id] = working_session

        # Background task: drain signal queue → send to running agent
        signal_task = asyncio.create_task(
            self._signal_drainer(working_session, runtime_id)
        )

        # Host-driven step loop with budget checks
        timeout = self.config.daemon.agent_timeout
        agent_spec = run_ctx.prompt_spec.agent_spec
        max_steps = agent_spec.max_steps
        stop_reason = "natural"
        step_count = 0

        logger.info(
            "Step loop started: ctx={} runtime_id={} agent={} timeout={}s max_steps={}",
            ctx_id, runtime_id, turn.agent_name, timeout, max_steps,
        )
        t0 = asyncio.get_running_loop().time()

        try:
            timeout_ctx = asyncio.timeout(timeout) if timeout else contextlib.nullcontext()
            async with timeout_ctx:
                async with contextlib.aclosing(working_session.step_iter()) as gen:
                    async for step in gen:
                        step_count += 1
                        if step.done:
                            logger.debug(
                                "Step loop done (natural): ctx={} runtime_id={} steps={}",
                                ctx_id, runtime_id, step_count,
                            )
                            break
                        if max_steps and step.rounds >= max_steps:
                            stop_reason = "max_steps"
                            logger.info(
                                "Step loop done (max_steps): ctx={} runtime_id={} steps={}",
                                ctx_id, runtime_id, step_count,
                            )
                            break
        except TimeoutError:
            stop_reason = "timeout"
            logger.warning(
                "Agent run timed out after {}s: ctx={} runtime_id={} steps={}",
                timeout, ctx_id, runtime_id, step_count,
            )
        finally:
            elapsed = asyncio.get_running_loop().time() - t0
            logger.info(
                "Step loop ended: ctx={} runtime_id={} reason={} steps={} elapsed={:.1f}s",
                ctx_id, runtime_id, stop_reason, step_count, elapsed,
            )
            signal_task.cancel()
            self._unregister_active_run(runtime_id, ctx_id)

        working_session.stop_reason = stop_reason
        return working_session

    async def _signal_drainer(self, session: Session, runtime_id: str) -> None:
        """Continuously drain signals and forward them to the running session."""
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
        runtime_id = f"agent-{turn.agent_name}-{ctx_id}" if ctx_id else f"agent-{turn.agent_name}-{task_id[:8]}"
        session = await self._launch(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
        )
        return self._last_assistant_text(session)

    async def _run_agent(
        self,
        agent_name: str,
        task: str,
        *,
        agent_env: dict[str, str],
        tool_names: list[str] | None = None,
        delegate_depth: int = 0,
    ) -> str:
        if agent_name not in CHARACTER_REGISTRY:
            raise ValueError(f"Unknown agent {agent_name!r}")
        run_env = dict(agent_env)
        run_env[env.AGENT_NAME] = agent_name
        turn = self._builder.build_delegated_turn(
            agent_name=agent_name,
            first_user_message=task,
            parent_env=AgentEnv(run_env),
        )
        return await self._run_simple_turn(
            turn,
            tool_names=tool_names,
            delegate_depth=delegate_depth,
        )

    async def start_delegate(
        self,
        *,
        parent: object,
        parent_run_id: str,
        agent: str,
        first_user_message: str,
        tools: list[str] | None,
        delegate_depth: int,
    ) -> Session:
        if delegate_depth > 3:
            raise DelegateDepthExceededError(
                max_depth=3,
                current_depth=delegate_depth,
                target_agent=agent,
            )
        parent_session = parent if isinstance(parent, Session) else None
        if parent_session is None:
            raise ValueError("delegate requires a parent session")
        active_parent = self._active_runs_by_runtime.get(parent_session.agent_id)
        caller_name = parent_session.agent_id if active_parent is None else active_parent.agent_name
        allowed = set()
        caller_char = CHARACTER_REGISTRY.get(caller_name)
        if caller_char is not None:
            allowed.update(caller_char.spec.subagents)
        if agent not in allowed:
            raise ValueError(f"Agent {caller_name!r} is not allowed to delegate to {agent!r}")
        parent_env = {} if active_parent is None else dict(active_parent.agent_env.values)
        task_id = self.runtime.new_task_id()
        runtime_id = f"delegate-{agent}-{task_id[:8]}"
        turn = self._builder.build_delegated_turn(
            agent_name=agent,
            first_user_message=first_user_message,
            parent_env=AgentEnv(parent_env),
        )
        bundle = await self._builder.build_task_bundle(turn)
        run_ctx = await self._builder.build_run_context(
            turn=turn,
            task_id=task_id,
            runtime_id=runtime_id,
            tool_names=tools,
            delegate_depth=delegate_depth,
        )
        launch = SessionLaunch.from_run_context(
            run_ctx,
            llm=self._make_llm(agent),
            manager=self,
        )
        session = launch.open()
        self._register_active_run(
            runtime_id=runtime_id,
            agent_name=agent,
            agent_env=run_ctx.agent_env,
            ctx_id=None,
        )
        session.start(bundle.startup_input)
        child_flow = session.flow
        if child_flow is not None:
            parent_session.attach_child_flow(parent_run_id, child_flow)
        self._delegate_sessions_by_run_id[parent_run_id] = session
        asyncio.create_task(
            self._wait_delegate_session(session, runtime_id),
            name=f"delegate-wait-{runtime_id}",
        )
        return session

    async def _wait_delegate_session(self, session: Session, runtime_id: str) -> None:
        try:
            async for _step in session.step_iter():
                pass
        finally:
            self._delegate_sessions_by_run_id = {
                run_id: child
                for run_id, child in self._delegate_sessions_by_run_id.items()
                if child is not session
            }
            self._unregister_active_run(runtime_id, None)

    def inspect_run(
        self,
        *,
        parent: object,
        run_id: str,
        limit: int = 200,
        max_chars: int = 4000,
    ) -> str:
        parent_session = parent if isinstance(parent, Session) else None
        if parent_session is None:
            return f"[ERROR] invalid parent session for run {run_id}"
        flow = parent_session.find_flow(run_id)
        if flow is None:
            if parent_session.agent is None:
                return f"[ERROR] parent session not started for run {run_id}"
            return f"[ERROR] unknown run id {run_id!r}"
        lines = [f"run_id: {flow.id}", f"kind: {flow.kind}"]
        tool_name = flow.info.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            lines.append(f"tool_name: {tool_name}")
        delegate = self._delegate_sessions_by_run_id.get(run_id)
        if delegate is not None:
            lines.append(f"delegate_status: {delegate.status.value}")
            delegate_flow = delegate.flow
            if delegate_flow is not None:
                from yuuagents.core.flow import render_agent_event

                lines.append("delegate_stem:")
                lines.append(delegate_flow.render(render_agent_event, limit=limit) or "<empty>")
        lines.append("stem:")
        lines.append(flow.render(str, limit=limit) or "<empty>")
        return self._truncate_text("\n".join(lines), max_chars)

    def cancel_run(
        self,
        *,
        parent: object,
        run_id: str,
    ) -> str:
        parent_session = parent if isinstance(parent, Session) else None
        if parent_session is None:
            return f"[ERROR] invalid parent session for run {run_id}"
        flow = parent_session.find_flow(run_id)
        if flow is None:
            if parent_session.agent is None:
                return f"[ERROR] parent session not started for run {run_id}"
            return f"[ERROR] unknown run id {run_id!r}"
        flow.cancel()
        delegate = self._delegate_sessions_by_run_id.get(run_id)
        if delegate is not None:
            delegate.status = AgentStatus.CANCELLED
        return f"Cancelled run {run_id}"

    def defer_run(
        self,
        *,
        parent: object,
        run_id: str,
        message: str,
    ) -> str:
        _ = parent
        delegate = self._delegate_sessions_by_run_id.get(run_id)
        if delegate is None:
            return f"[ERROR] run {run_id!r} is not a delegated agent"
        prompt = (
            message.strip()
            or "请立即停止等待中的前台工具，把当前工作移到后台，并先汇报简短进展。"
        )
        delegate.send(yuullm.user(prompt), defer_tools=True)
        return f"Sent defer signal to delegated run {run_id}"

    async def input_run(
        self,
        *,
        parent: object,
        run_id: str,
        data: str,
        append_newline: bool = True,
    ) -> str:
        parent_session = parent if isinstance(parent, Session) else None
        if parent_session is None:
            return f"[ERROR] invalid parent session for run {run_id}"
        delegate = self._delegate_sessions_by_run_id.get(run_id)
        if delegate is not None:
            delegate.send(yuullm.user(data))
            return f"Input sent to delegated run {run_id}"
        flow = parent_session.find_flow(run_id)
        if flow is None:
            if parent_session.agent is None:
                return f"[ERROR] parent session not started for run {run_id}"
            return f"[ERROR] unknown run id {run_id!r}"
        tool_name = flow.info.get("tool_name")
        if tool_name != "execute_bash":
            return f"[ERROR] run {run_id!r} does not accept input"
        docker = parent_session.context.docker
        container = parent_session.context.docker_container
        if docker is None or not container:
            return f"[ERROR] docker terminal unavailable for run {run_id!r}"
        return await docker.write_terminal(
            container,
            run_id,
            data,
            append_newline=append_newline,
        )

    async def wait_runs(
        self,
        *,
        parent: object,
        run_ids: list[str],
    ) -> str:
        parent_session = parent if isinstance(parent, Session) else None
        if parent_session is None:
            return "[ERROR] invalid parent session for wait"
        if parent_session.agent is None:
            return "[ERROR] parent session not started for wait"
        if not run_ids:
            return "[ERROR] run_ids must not be empty"

        waits: list[Any] = []
        for run_id in run_ids:
            delegate = self._delegate_sessions_by_run_id.get(run_id)
            if delegate is not None:
                if delegate.agent is None:
                    return f"[ERROR] delegated run {run_id!r} not started"
                waits.append(delegate.wait())
                continue
            flow = parent_session.find_flow(run_id)
            if flow is None:
                return f"[ERROR] unknown run id {run_id!r}"
            waits.append(flow.wait())

        await asyncio.gather(*waits)
        return f"Wait finished for runs: {', '.join(run_ids)}"

    async def run_conversation(
        self,
        message: InboundMessage,
        *,
        agent_name: str = "main",
        user_role: str = "",
        session: object | None = None,
        handoff_text: str = "",
        text_override: str = "",
    ) -> Session | None:
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
        except BaseException:
            logger.exception("agent failed: ctx={} agent={} task_id={}", turn.message.ctx_id, agent_name, task_id)
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
            first_user_message=full_task,
            parent_env=base_env,
            startup_kind="scheduled",
        )
        try:
            await self._launch(
                turn=turn,
                task_id=task_id,
                runtime_id=f"yuubot-cron-{agent_name}-{ctx_id}" if ctx_id else f"yuubot-cron-{agent_name}-{task_id[:8]}",
            )
        except BaseException:
            logger.exception("Scheduled agent failed: {}", task)
