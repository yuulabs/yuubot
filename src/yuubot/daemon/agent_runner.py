"""RFC2 AgentRunner — host-driven yuuagents step loop skeleton."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from pathlib import Path

import attrs
import httpx
from loguru import logger
import yuullm
import yuuagents as ya

from yuubot.auth import bot_kind_for_message
from yuubot.characters import CHARACTER_REGISTRY, get_character
from yuubot.config import Config
from yuubot.core.models import Message, ReplySegment, TextSegment
from yuubot.core.onebot import build_send_msg
from yuubot.core.types import InboundMessage, Sender
from yuubot.daemon.conversation import Conversation
from yuubot.daemon.llm_factory import make_resolved_llm
from yuubot.daemon.restricted_python import RestrictedPythonSession, RestrictedPythonWorker
from yuubot.daemon.runtime import YuubotRuntimeFactory, python_backend_for_bot_kind
from yuubot.daemon.runtime_session import RuntimeSession


@attrs.define(frozen=True)
class ActiveRun:
    runtime_id: str
    ctx_id: int
    agent_name: str
    task_id: str


class AgentRunner:
    """Create and drive yuuagents live agents for yuubot conversations."""

    def __init__(
        self,
        config: Config,
        *,
        engine: ya.Engine | None = None,
        master_engine: ya.Engine | None = None,
        group_engine: ya.Engine | None = None,
        runtime_factory: YuubotRuntimeFactory | None = None,
    ) -> None:
        self.config = config
        self.runtime_factory = runtime_factory or YuubotRuntimeFactory(config)
        if engine is not None:
            self.master_engine = engine
            self.group_engine = engine
        else:
            self.master_engine = master_engine or self.runtime_factory.create_engine()
            self.group_engine = group_engine or self.runtime_factory.create_engine()
        self.engine = self.master_engine
        self._active_runs_by_ctx: dict[int, ActiveRun] = {}
        self._sessions_by_runtime: dict[str, RuntimeSession] = {}
        self._engines_by_runtime: dict[str, ya.Engine] = {}
        self._signal_queues: dict[str, asyncio.Queue[yuullm.Message]] = {}
        self._private_python_sessions: dict[str, ya.PythonSession] = {}
        self._restricted_python_worker = RestrictedPythonWorker(default_timeout_s=self._restricted_timeout_s())

    async def stop(self) -> None:
        for session in list(self._sessions_by_runtime.values()):
            with contextlib.suppress(Exception):
                await session.close()
        for session in list(self._private_python_sessions.values()):
            with contextlib.suppress(Exception):
                await session.close()
        self._sessions_by_runtime.clear()
        self._engines_by_runtime.clear()
        self._active_runs_by_ctx.clear()
        self._signal_queues.clear()
        self._private_python_sessions.clear()
        self._restricted_python_worker.stop()
        await self.master_engine.close()
        if self.group_engine is not self.master_engine:
            await self.group_engine.close()

    async def _make_llm(self, agent_name: str) -> tuple[yuullm.YLLMClient, bool]:
        llm, resolved = await make_resolved_llm(agent_name, self.config)
        return llm, resolved.supports_vision

    def get_active_run(self, ctx_id: int) -> ActiveRun | None:
        return self._active_runs_by_ctx.get(ctx_id)

    def cancel_ctx(self, ctx_id: int) -> bool:
        active = self._active_runs_by_ctx.pop(ctx_id, None)
        if active is None:
            return False
        session = self._sessions_by_runtime.get(active.runtime_id)
        if session is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(session.close())
            except RuntimeError:
                pass
        logger.info("RFC2 run cancelled for ctx={}", ctx_id)
        return True

    def enqueue_signal(self, runtime_id: str, msg: yuullm.Message) -> None:
        queue = self._signal_queues.get(runtime_id)
        if queue is None:
            return
        while not queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(msg)

    async def render_signal(self, msg: InboundMessage, *, upto_row_id: int = 0) -> yuullm.Message:
        del upto_row_id
        await _resolve_replies(msg.segments)
        return yuullm.user(*_message_content(msg))

    async def run_scheduled(
        self,
        task: str,
        ctx_id: int | None,
        *,
        agent_name: str = "yuu",
    ) -> None:
        from yuubot.core.models import Context

        if ctx_id is None:
            ctx, _ = await Context.get_or_create(type="private", target_id=self.config.bot.master)
            ctx_id = int(ctx.id)
            chat_type = "private"
            target_id = self.config.bot.master
            group_id = 0
        else:
            ctx = await Context.get_or_none(id=ctx_id)
            if ctx is None:
                logger.warning("Scheduled task skipped; ctx={} does not exist", ctx_id)
                return
            chat_type = "group" if ctx.type == "group" else "private"
            target_id = int(ctx.target_id)
            group_id = target_id if chat_type == "group" else 0

        inbound = InboundMessage(
            message_id=0,
            ctx_id=ctx_id,
            chat_type=chat_type,
            group_id=group_id,
            self_id=self.config.bot.qq,
            sender=Sender(user_id=self.config.bot.master, nickname="scheduler"),
            segments=[TextSegment(text=task)],
            timestamp=0,
            raw_message=task,
            raw_event={"post_type": "message", "message_type": chat_type, "scheduled": True},
        )
        bot_kind = "master" if chat_type == "private" and target_id == self.config.bot.master else "group"
        await self.run_conversation(
            inbound,
            agent_name=agent_name,
            bot_kind=bot_kind,
            text_override=task,
            handoff_text="这是一个定时任务触发。请完成任务，必要时直接回复到绑定的 QQ 上下文。",
        )

    async def run_conversation(
        self,
        message: InboundMessage,
        *,
        agent_name: str = "yuu",
        bot_kind: str | None = None,
        session: Conversation | None = None,
        handoff_text: str = "",
        text_override: str | None = None,
        recent_ctx_upto_row_id: int = 0,
        send_reply: bool = True,
    ) -> RuntimeSession | None:
        del recent_ctx_upto_row_id
        if agent_name not in CHARACTER_REGISTRY:
            raise KeyError(f"unknown agent: {agent_name}")
        bot_kind = bot_kind or bot_kind_for_message(message, self.config.bot.master)
        if text_override is None:
            await _resolve_replies(message.segments)

        character = get_character(agent_name)
        existing_session = session.session if session is not None else None
        runtime_session = existing_session if isinstance(existing_session, RuntimeSession) else None
        if runtime_session is not None and not runtime_session.closed and runtime_session.agent.error is None:
            user_content = _message_content(
                message,
                text_override=text_override,
                handoff_text=handoff_text,
            )
            runtime_session.agent.append_message(yuullm.user(*user_content))
        else:
            llm, supports_vision = await self._make_llm(agent_name)
            engine = self._engine_for_bot_kind(bot_kind)
            conversation_id = _conversation_id(message.ctx_id, agent_name)
            task_id = (
                runtime_session.task_id
                if runtime_session is not None
                else ""
            ) or _task_id()
            definition = self.runtime_factory.build_definition(
                character,
                llm,
                bot_kind=bot_kind,
                supports_vision=supports_vision,
            )
            runtime = self.runtime_factory.build_runtime(
                character,
                message,
                conversation_id=conversation_id,
                task_id=task_id,
                bot_kind=bot_kind,
                supports_vision=supports_vision,
            )
            if (
                runtime_session is not None
                and runtime_session.snapshot is not None
                and runtime_session.agent.error is None
            ):
                agent = engine.restore_agent(definition, runtime_session.snapshot, runtime=runtime)
                self._attach_python_session(agent, message, agent_name=agent_name, bot_kind=bot_kind)
                runtime_session.agent = agent
                runtime_session.supports_vision = supports_vision
                runtime_session.final_text = ""
                runtime_session.steps.clear()
                user_content = _message_content(
                    message,
                    text_override=text_override,
                    handoff_text=handoff_text,
                )
                runtime_session.agent.append_message(yuullm.user(*user_content))
            else:
                user_content = _message_content(
                    message,
                    text_override=text_override,
                    handoff_text=handoff_text,
                    include_time_prefix=True,
                    bootstrap_prefix=_load_bootstrap(
                        self.runtime_factory._workspace_root(message.ctx_id)
                    ) if agent_name == "maid" else "",
                )
                agent = engine.create_agent(definition, yuullm.user(*user_content), runtime=runtime)
                self._attach_python_session(agent, message, agent_name=agent_name, bot_kind=bot_kind)
                runtime_session = RuntimeSession(
                    agent=agent,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    supports_vision=supports_vision,
                    task_id=task_id,
                )
            self.runtime_factory.bind_agent_metadata(
                agent.id,
                message=message,
                conversation_id=conversation_id,
                character_name=agent_name,
                task_id=task_id,
            )

        active = ActiveRun(
            runtime_id=runtime_session.agent.id,
            ctx_id=message.ctx_id,
            agent_name=agent_name,
            task_id=runtime_session.task_id,
        )
        self._active_runs_by_ctx[message.ctx_id] = active
        self._sessions_by_runtime[runtime_session.agent.id] = runtime_session
        self._engines_by_runtime[runtime_session.agent.id] = self._engine_for_bot_kind(bot_kind)
        self._signal_queues.setdefault(runtime_session.agent.id, asyncio.Queue())
        runtime_session.status = "running"

        try:
            await self._drive_session(runtime_session, message)
            logger.info(
                "Drive session done: ctx={} agent={} final_text={!r} status={}",
                message.ctx_id,
                agent_name,
                (runtime_session.final_text or "")[:100],
                runtime_session.status,
            )
            if send_reply and runtime_session.final_text:
                await self._send_text_reply(message, runtime_session.final_text)
            if not runtime_session.agent.closed:
                engine = self._engines_by_runtime.get(runtime_session.agent.id) or self._engine_for_bot_kind(bot_kind)
                runtime_session.snapshot = engine.save_agent(runtime_session.agent)
            return runtime_session
        finally:
            runtime_session.status = "idle" if runtime_session.status == "running" else runtime_session.status
            self._active_runs_by_ctx.pop(message.ctx_id, None)

    def live_agent_count(self) -> int:
        total = len(self.master_engine.live_agents)
        if self.group_engine is not self.master_engine:
            total += len(self.group_engine.live_agents)
        return total

    def _engine_for_bot_kind(self, bot_kind: str) -> ya.Engine:
        if python_backend_for_bot_kind(bot_kind) == "kernel":
            return self.master_engine
        return self.group_engine

    async def _drive_session(
        self,
        runtime_session: RuntimeSession,
        message: InboundMessage,
    ) -> None:
        character = get_character(runtime_session.agent_name)
        max_turns = character.spec.max_turns
        timeout_s = (
            character.spec.inactivity_timeout_s
            or self.config.daemon.agent_timeout
            or None
        )
        iterator = runtime_session.agent.steps(max_turns=max_turns).__aiter__()
        while True:
            try:
                if timeout_s:
                    async with asyncio.timeout(float(timeout_s)):
                        step = await anext(iterator)
                else:
                    step = await anext(iterator)
            except StopAsyncIteration:
                break
            except TimeoutError:
                runtime_session.status = "timeout"
                runtime_session.stop_reason = "timeout"
                await runtime_session.agent.interrupt_python()
                await runtime_session.agent.close()
                logger.warning(
                    "RFC2 agent inactivity timeout: ctx={} agent={} task={}",
                    message.ctx_id,
                    runtime_session.agent_name,
                    runtime_session.task_id,
                )
                break

            runtime_session.steps.append(step)
            if isinstance(step, ya.LlmStep):
                runtime_session.update_usage(step.usage)
                if step.tool_calls:
                    logger.info(
                        "LLM step [tool_calls]: ctx={} agent={} calls={}",
                        message.ctx_id,
                        runtime_session.agent_name,
                        [(tc.name, str(tc.arguments)[:120]) for tc in step.tool_calls],
                    )
                else:
                    logger.info(
                        "LLM step [text]: ctx={} agent={} text={!r}",
                        message.ctx_id,
                        runtime_session.agent_name,
                        step.text[:200] if step.text else "",
                    )
                if not step.tool_calls and step.text.strip():
                    runtime_session.final_text = step.text.strip()
            elif isinstance(step, ya.ToolStep):
                logger.info(
                    "Tool step: ctx={} agent={} tool={} ok={} output={!r}",
                    message.ctx_id,
                    runtime_session.agent_name,
                    step.tool_name,
                    step.error is None,
                    step.output_text[:800] if step.output_text else "",
                )
            elif isinstance(step, ya.ErrorStep):
                runtime_session.status = "error"
                runtime_session.stop_reason = "error"
                runtime_session.final_text = f"Agent 运行出错：{step.type}: {step.message}"
                logger.error(
                    "Error step: ctx={} agent={} type={} msg={}",
                    message.ctx_id,
                    runtime_session.agent_name,
                    step.type,
                    step.message,
                )

            await self._drain_signals(runtime_session)

    async def _drain_signals(self, runtime_session: RuntimeSession) -> None:
        queue = self._signal_queues.get(runtime_session.agent.id)
        if queue is None:
            return
        while not queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                runtime_session.agent.append_message(queue.get_nowait())

    async def _send_text_reply(self, message: InboundMessage, text: str) -> None:
        segments: Message = [TextSegment(text=text)]
        target_id = message.group_id if message.chat_type == "group" else message.sender.user_id
        body = build_send_msg(message.chat_type, target_id, segments)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{self.config.daemon.recorder_api}/send_msg", json=body)
        except Exception:
            logger.exception("Failed to send RFC2 agent reply")

    def _attach_python_session(
        self,
        agent: ya.Agent,
        message: InboundMessage,
        *,
        agent_name: str,
        bot_kind: str,
    ) -> None:
        runtime = agent.runtime.python
        if runtime is None:
            return
        if python_backend_for_bot_kind(bot_kind) == "kernel":
            key = f"private:{message.sender.user_id}:{agent_name}"
            session = self._private_python_sessions.get(key)
            if session is None:
                session = ya.PythonSession(
                    agent_id=agent.id,
                    agent_name=agent_name,
                    runtime=runtime,
                )
                self._private_python_sessions[key] = session
            agent.python_session = session
            agent.owns_python_session = False
            return
        agent.python_session = RestrictedPythonSession(
            worker=self._restricted_python_worker,
            session_id=f"{message.chat_type}:{message.ctx_id}:{agent_name}",
            runtime=runtime,
            agent_id=agent.id,
            agent_name=agent_name,
        )
        agent.owns_python_session = False

    def _restricted_timeout_s(self) -> float:
        raw = self.config.yuuagents.get("restricted_python", {})
        if not isinstance(raw, dict):
            return 8.0
        value = raw.get("timeout_s", 8.0)
        try:
            return max(0.1, float(value))
        except (TypeError, ValueError):
            return 8.0


def _load_bootstrap(workspace: Path) -> str:
    bootstrap_path = workspace / "BOOTSTRAP.md"
    if not bootstrap_path.exists():
        bootstrap_path.write_text(
            "# BOOTSTRAP\n_更新：（尚未初始化）_\n\n"
            "## 活跃项目\n（暂无）\n\n"
            "## 记忆指针\n（暂无）\n\n"
            "## 待办\n（暂无）\n\n"
            "## 近期历史\n（暂无）\n",
            encoding="utf-8",
        )
    return bootstrap_path.read_text(encoding="utf-8")


def _message_content(
    message: InboundMessage,
    *,
    text_override: str | None = None,
    handoff_text: str = "",
    include_time_prefix: bool = False,
    bootstrap_prefix: str = "",
) -> yuullm.Content:
    from yuubot.rendering import render_message_xml

    items: yuullm.Content = []

    if bootstrap_prefix:
        items.append(yuullm.TextItem(
            type="text",
            text=f"[bootstrap.md]\n{bootstrap_prefix.strip()}\n[/bootstrap.md]",
        ))

    if include_time_prefix:
        now_text = datetime.now().astimezone().strftime("%Y年%m月%d日 %H时%M分%S秒")
        items.append(yuullm.TextItem(type="text", text=f"现在是 {now_text}"))

    if handoff_text:
        items.append(yuullm.TextItem(type="text", text=handoff_text.strip()))

    segments = (
        [TextSegment(text=text_override.strip() or "（空消息）")]
        if text_override is not None
        else message.segments
    )
    xml_parts = [render_message_xml(
        uid=message.sender.user_id,
        name=message.sender.nickname,
        display_name=message.sender.card,
        time=message.timestamp,
        segments=segments,
        message_id=message.message_id or None,
    )]
    for extra in message.extra_messages:
        xml_parts.append(render_message_xml(
            uid=extra.sender.user_id,
            name=extra.sender.nickname,
            display_name=extra.sender.card,
            time=extra.timestamp,
            segments=extra.segments,
            message_id=extra.message_id or None,
        ))
    items.append(yuullm.TextItem(type="text", text="\n".join(xml_parts)))

    return items


async def _resolve_replies(segments: list) -> None:
    from yuubot.core.models import MessageRecord
    for i, seg in enumerate(segments):
        if isinstance(seg, ReplySegment) and seg.id and not seg.content:
            try:
                record = await MessageRecord.filter(message_id=int(seg.id)).order_by("-id").first()
                if record:
                    segments[i] = ReplySegment(id=seg.id, content=record.content)
            except Exception:
                pass


def _conversation_id(ctx_id: int, agent_name: str) -> str:
    return f"ctx-{ctx_id}-{agent_name}"


def _task_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]
