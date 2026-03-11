"""Message dispatcher — command parsing, permission check, agent trigger."""

import asyncio
import re

import httpx

from yuubot.commands.builtin import _exec_llm
from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import RootCommand, MatchResult
from yuubot.config import Config
from yuubot.core.models import Role, ReplySegment, AtSegment, segments_to_plain
from yuubot.core.onebot import parse_segments, build_send_msg
from yuubot.daemon.session import SessionManager
from yuuagents.flow import Ping, PingKind

from loguru import logger

_AGENT_TAG_RE = re.compile(r"^#(\w+)\s*")


_CURATOR_MIN_TURNS = 3      # LLM exchanges (user+assistant pairs)
_CURATOR_MIN_SECONDS = 60   # wall-clock duration


def _session_worth_curating(session) -> bool:
    """True if the session is substantial enough for the curator to bother."""
    duration = session.last_active - session.created_at
    # Count assistant turns as a proxy for exchanges
    turns = sum(1 for role, _ in session.history if role == "assistant")
    return turns >= _CURATOR_MIN_TURNS and duration >= _CURATOR_MIN_SECONDS


def _parse_agent_tag(text: str) -> tuple[str, str]:
    """Parse optional ``#agent_name`` prefix from text.

    Returns ``(agent_name, remaining_text)``.
    If no tag found, returns ``("main", original_text)``.
    """
    m = _AGENT_TAG_RE.match(text.strip())
    if m:
        agent_name = m.group(1)
        remaining = text.strip()[m.end():]
        return agent_name, remaining
    return "main", text


def _ctx_key(event: dict) -> str:
    """Derive a context key from an event for per-ctx queuing."""
    msg_type = event.get("message_type", "private")
    if msg_type == "group":
        return f"group:{event.get('group_id', 0)}"
    return f"private:{event.get('user_id', 0)}"


class _CtxWorker:
    """Per-ctx sequential worker. One context, one agent at a time."""

    def __init__(self, key: str, dispatcher: Dispatcher) -> None:
        self.key = key
        self.dispatcher = dispatcher
        self.queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                first = await self.queue.get()
                # Drain queued items to debounce consecutive continuations
                batch = [first]
                while not self.queue.empty():
                    batch.append(self.queue.get_nowait())

                groups = _group_batch(batch)
                for group in groups:
                    match, event = group[0]
                    if len(group) > 1:
                        event["_extra_events"] = [e for _, e in group[1:]]
                    try:
                        await self.dispatcher._handle(match, event)
                    except Exception:
                        logger.exception("Error handling command in ctx={}", self.key)

                for _ in batch:
                    self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop error in ctx={}", continuing, self.key)


def _group_batch(batch: list) -> list[list]:
    """Group consecutive continuation items (match is None).
    Non-continuations stay as single-item groups."""
    groups: list[list] = []
    current = [batch[0]]
    for item in batch[1:]:
        match, _ = item
        prev_match, _ = current[0]
        if match is None and prev_match is None:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    groups.append(current)
    return groups


class Dispatcher:
    """Receives events, matches commands, dispatches to executors or agent."""


    def __init__(
        self,
        config: Config,
        root: RootCommand,
        role_mgr: RoleManager,
        deps: dict,
        agent_runner,
        session_mgr: SessionManager | None = None,
    ) -> None:
        self.config = config
        self.root = root
        self.role_mgr = role_mgr
        self.deps = deps
        self.agent_runner = agent_runner
        self.session_mgr = session_mgr or SessionManager()
        self._workers: dict[str, _CtxWorker] = {}

    def start(self) -> None:
        pass  # workers are created lazily on first message per ctx

    async def stop(self) -> None:
        for w in list(self._workers.values()):
            await w.stop()
        self._workers.clear()

    async def dispatch(self, event: dict) -> None:
        """Called by WS client for each incoming event."""
        if event.get("post_type") != "message":
            return

        # Lazily collect expired sessions and trigger curator for substantial ones
        for expired in self.session_mgr.collect_expired():
            if _session_worth_curating(expired):
                asyncio.create_task(self._run_curator(
                    expired.history, expired.ctx_id, expired.user_id,
                ))

        user_id = event.get("user_id", 0)
        group_id = event.get("group_id", 0)
        msg_type = event.get("message_type", "unknown")
        ctx_id = event.get("ctx_id", 0)

        logger.debug("event: type={} user={} group={} ctx={}", msg_type, user_id, group_id, ctx_id)

        # Self-ignore early (avoid unnecessary work)
        if user_id == self.config.bot.qq:
            return

        # Extract plain text
        segments = parse_segments(event.get("message", []))
        bot_qq = str(self.config.bot.qq)
        replies = [s for s in segments if isinstance(s, ReplySegment)]
        others = [
            s for s in segments
            if not isinstance(s, ReplySegment)
            and not (isinstance(s, AtSegment) and s.qq == bot_qq)
        ]
        plain = segments_to_plain(others + replies).strip()
        logger.info("Message text: {}", plain)

        # Try command match early — non-LLM commands bypass session entirely.
        # Session is an LLM-only concept; built-in commands (/ping, /cost, /help, etc.)
        # respond immediately without touching session state.
        cmd_match = self.root.match_message(plain)
        is_llm_match = cmd_match is not None and cmd_match.command.executor is _exec_llm

        if cmd_match is not None and not is_llm_match:
            # Non-LLM command: respond immediately without touching session state.
            # Session is LLM-only — built-in commands (/ping, /cost, /help, etc.)
            # execute independently regardless of whether a session is active.
            should_respond = await self._should_respond(event)
            if not should_respond:
                return
            scope = str(event.get("group_id", "global"))
            role = await self.role_mgr.get(event["user_id"], scope)
            if not cmd_match.command.check_permission(role):
                logger.info("Permission denied (non-llm): user={} role={} cmd={}", user_id, role, cmd_match.command.prefix)
                return
            logger.info("Command accepted (non-llm): user={} cmd={}", user_id, cmd_match.command.prefix)
            # Execute inline — non-LLM commands are fast and must not queue
            # behind slow LLM agent runs in the per-ctx worker.
            try:
                reply = await cmd_match.command.executor(cmd_match.remaining, event, self.deps)
                if reply:
                    await self._send_reply(event, reply)
            except Exception:
                logger.exception("Error executing non-llm command: {}", cmd_match.command.prefix)
            return

        # LLM / session path — check whether we should respond in this context.
        should_respond = await self._should_respond(event)
        logger.info("should_respond check: user={} group={} type={} result={}", user_id, group_id, msg_type, should_respond)
        if not should_respond:
            return

        ctx_id = event.get("ctx_id", 0)
        is_auto = self.session_mgr.is_auto(ctx_id)
        session = self.session_mgr.get(ctx_id)

        if session is not None:
            # Active session — route LLM messages as continuation.
            if is_llm_match and is_auto:
                # Auto mode: /yllm is a switch only if it names a different agent.
                tag_agent, _ = _parse_agent_tag(cmd_match.remaining)
                if tag_agent == session.agent_name:
                    # Same agent — treat as continuation
                    self.session_mgr.touch(session)
                    logger.info("Session continuation (auto/yllm): ctx={} agent={}", ctx_id, session.agent_name)
                    event["_session"] = session
                    event["_session_agent"] = session.agent_name
                    event["_session_remaining"] = cmd_match.remaining
                    await self._ping_or_enqueue(session, event)
                    return
                # Different agent — fall through to normal /yllm handling (switch)
                match = cmd_match
            elif msg_type == "group" and not self._is_at_bot(event) and not is_llm_match:
                # Group chat, no @bot, not a /yllm command — ignore for session
                return
            else:
                # Continue session: @bot, private chat, or /yllm all count
                remaining = plain
                if is_llm_match:
                    remaining = cmd_match.remaining
                self.session_mgr.touch(session)
                logger.info("Session continuation: ctx={} agent={}", ctx_id, session.agent_name)
                event["_session"] = session
                event["_session_agent"] = session.agent_name
                event["_session_remaining"] = remaining
                await self._ping_or_enqueue(session, event)
                return
        elif is_auto:
            # Auto mode, no active session: auto-resume or /yllm switch
            cur_agent = self.session_mgr.current_agent(ctx_id)

            if is_llm_match:
                # Explicit /yllm → switch/start agent
                match = cmd_match
            elif cur_agent is not None and cmd_match is None:
                # Auto-resume: session expired but we know the current agent
                logger.info("Auto mode resume: ctx={} agent={}", ctx_id, cur_agent)
                new_session = self.session_mgr.create(
                    ctx_id, cur_agent, user_id=event.get("user_id", 0),
                )
                event["_session"] = new_session
                event["_session_agent"] = cur_agent
                event["_session_remaining"] = plain
                self._enqueue(None, event)
                return
            else:
                logger.info("Auto mode: no agent selected yet, ignoring: {}", plain)
                return
        else:
            # No active session — must be an LLM command (non-LLM already handled above)
            if cmd_match is None:
                logger.info("No command match for: {}", plain)
                return
            match = cmd_match

        logger.info("Matched command: {}", match.command.prefix)

        # Permission check (LLM path)
        scope = str(event.get("group_id", "global"))
        role = await self.role_mgr.get(event["user_id"], scope)

        is_free_mode = await self._is_free_mode(event)
        is_llm_cmd = match.command.executor is _exec_llm

        logger.info("Permission check: user={} role={} cmd={} free_mode={} is_llm={}",
                 user_id, role, match.command.prefix, is_free_mode, is_llm_cmd)

        if not (is_free_mode and is_llm_cmd):
            if not match.command.check_permission(role):
                logger.info("Permission denied: user={} role={} cmd={}", event["user_id"], role, match.command.prefix)
                return

        logger.info("Command accepted: user={} cmd={}", user_id, match.command.prefix)
        self._enqueue(match, event)

    def _enqueue(self, match, event: dict) -> None:
        """Put a (match, event) pair into the per-ctx worker queue."""
        key = _ctx_key(event)
        if key not in self._workers:
            w = _CtxWorker(key, self)
            self._workers[key] = w
            w.start()
        self._workers[key].queue.put_nowait((match, event))

    async def _build_ping_payload(self, event: dict) -> str:
        """Build XML payload for a ping, same format as continuation task."""
        from datetime import datetime, timezone
        from yuubot.core.models import AtSegment as _AtSeg, segments_to_json
        from yuubot.skills.im.formatter import (
            format_message_to_xml,
            get_user_alias,
        )

        ctx_id = event.get("ctx_id", "?")
        segments = parse_segments(event.get("message", []))
        bot_qq = str(self.config.bot.qq)
        segments = [s for s in segments if not (isinstance(s, _AtSeg) and s.qq == bot_qq)]

        bot_name = await self.agent_runner._get_bot_name()
        segments = self.agent_runner._replace_command_prefix(segments, bot_name)

        user_id = event.get("user_id", "?")
        nickname = event.get("sender", {}).get("nickname", "")
        alias = await get_user_alias(user_id, ctx_id)
        display_name = event.get("sender", {}).get("card", "")
        ts = datetime.fromtimestamp(event.get("time", 0), tz=timezone.utc)
        raw_json = segments_to_json(segments)

        return await format_message_to_xml(
            msg_id=event.get("message_id", 0),
            user_id=user_id,
            nickname=nickname,
            display_name=display_name,
            alias=alias,
            timestamp=ts,
            raw_message=raw_json,
            media_files=event.get("media_files", []),
            ctx_id=int(ctx_id) if ctx_id != "?" else None,
        )

    async def _ping_or_enqueue(self, session, event: dict) -> None:
        """If a root flow is running for this ctx, ping it; otherwise enqueue."""
        ctx_id = event.get("ctx_id", 0)
        root_flow = self.agent_runner.get_active_flow(ctx_id)
        if root_flow is not None:
            payload = await self._build_ping_payload(event)
            # Re-check: flow might have finished during async payload build
            if self.agent_runner.get_active_flow(ctx_id) is root_flow:
                self.session_mgr.touch(session)
                root_flow.ping(Ping(
                    kind=PingKind.USER_MESSAGE,
                    source_flow_id="dispatcher",
                    payload=payload,
                ))
                logger.info("Pinged running flow for ctx={}", ctx_id)
                return
        # Flow not running — fall through to _CtxWorker continuation
        self._enqueue(None, event)

    async def _handle(self, match, event: dict) -> None:
        # Session continuation — match is None, session info in event
        session = event.pop("_session", None)
        agent_name = event.pop("_session_agent", None)
        session_remaining = event.pop("_session_remaining", None)

        if session is not None and agent_name is not None:
            # Validate agent still exists
            if not self._agent_exists(agent_name):
                await self._send_reply(event, f"未知 Agent: {agent_name}")
                return

            scope = str(event.get("group_id", "global"))
            role = await self.role_mgr.get(event["user_id"], scope)
            required_role = self.config.agent_min_role(agent_name)
            if role < required_role:
                await self._send_reply(
                    event,
                    f"权限不足: Agent {agent_name!r} 需要 {required_role.name} 权限",
                )
                return

            ctx_id = event.get("ctx_id", 0)
            # Build a minimal MatchResult for agent_runner
            from yuubot.commands.tree import Command as _Cmd
            synth_match = MatchResult(
                command=_Cmd(prefix="llm", executor=_exec_llm),
                remaining=session_remaining or "",
                entry="",
            )
            history, tokens, task_id = await self.agent_runner.run(
                synth_match, event,
                agent_name=agent_name,
                user_role=role.name,
                session=session,
            )
            session.task_id = task_id
            await self._finish_turn(session, history, tokens, event)
            return

        # Normal command handling
        if match is None:
            return

        executor = match.command.executor
        if executor is _exec_llm:
            # New /yllm invocation — parse #agent_name
            llm_agent_name, remaining = _parse_agent_tag(match.remaining)
            match.remaining = remaining

            if not self._agent_exists(llm_agent_name):
                await self._send_reply(event, f"未知 Agent: {llm_agent_name}")
                return

            scope = str(event.get("group_id", "global"))
            role = await self.role_mgr.get(event["user_id"], scope)
            required_role = self.config.agent_min_role(llm_agent_name)
            if role < required_role:
                await self._send_reply(
                    event,
                    f"权限不足: Agent {llm_agent_name!r} 需要 {required_role.name} 权限",
                )
                return

            ctx_id = event.get("ctx_id", 0)
            new_session = self.session_mgr.create(
                ctx_id, llm_agent_name, user_id=event.get("user_id", 0),
            )

            history, tokens, task_id = await self.agent_runner.run(
                match, event,
                agent_name=llm_agent_name,
                user_role=role.name,
                session=new_session,
            )
            new_session.task_id = task_id
            await self._finish_turn(new_session, history, tokens, event)

        elif executor is not None:
            reply = await executor(match.remaining, event, self.deps)
            if reply:
                await self._send_reply(event, reply)

    async def _finish_turn(
        self, session, history: list, tokens: int, event: dict
    ) -> None:
        """Update session history and handle token-limit rollover.

        On rollover: summarize the old session, create a fresh session carrying
        the summary as a handoff note, and notify the user.
        """
        rolled = self.session_mgr.update_history(session, history, tokens)

        if not rolled:
            return

        ctx_id = session.ctx_id
        agent_name = session.agent_name
        user_id = session.user_id
        worth_curating = _session_worth_curating(session)

        await self._send_reply(event, "（上下文已满，正在压缩摘要，稍后继续...）")

        try:
            note = await self.agent_runner.summarize(history, agent_name)
        except Exception:
            logger.exception("Failed to summarize session for ctx={}", ctx_id)
            note = ""

        new_session = self.session_mgr.create(ctx_id, agent_name, user_id=user_id)
        new_session.handoff_note = note
        logger.info("Session rolled over: ctx={} agent={} note_len={}", ctx_id, agent_name, len(note))

        if note:
            await self._send_reply(event, "（已压缩上下文，新会话已就绪，可继续对话）")

        if worth_curating:
            asyncio.create_task(self._run_curator(history, ctx_id, user_id))

    async def _run_curator(self, history: list, ctx_id: int, user_id: int) -> None:
        """Run mem_curator as a background task after session rollover."""
        try:
            await self.agent_runner.curate(history, ctx_id, user_id)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)

    async def _send_reply(self, event: dict, text: str) -> None:
        """Send a text reply back to the source context."""
        from yuubot.core.models import TextSegment
        segments = [TextSegment(text=text)]
        msg_type = event.get("message_type", "private")
        target_id = event.get("group_id", 0) if msg_type == "group" else event.get("user_id", 0)
        body = build_send_msg(msg_type, target_id, segments)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{self.config.daemon.recorder_api}/send_msg", json=body)
        except Exception:
            logger.exception("Failed to send reply")

    def _is_at_bot(self, event: dict) -> bool:
        """Check if the message contains an @bot segment."""
        bot_qq = str(self.config.bot.qq)
        for seg in event.get("message", []):
            if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == bot_qq:
                return True
        return False

    async def _should_respond(self, event: dict) -> bool:
        """Check response mode (at/free) and DM whitelist."""
        msg_type = event.get("message_type")
        user_id = event.get("user_id", 0)

        if user_id == self.config.bot.qq:
            return False  # ignore self
        if user_id == self.config.bot.master:
            return True

        # If there's an active session for this ctx, respond to continue it
        ctx_id = event.get("ctx_id", 0)
        if ctx_id and self.session_mgr.get(ctx_id) is not None:
            if msg_type == "private":
                return True
            if msg_type == "group":
                # In group, still require @bot to continue session
                bot_qq = str(self.config.bot.qq)
                for seg in event.get("message", []):
                    if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == bot_qq:
                        return True

        if msg_type == "private":
            return user_id in self.config.response.dm_whitelist

        if msg_type == "group":
            gid = event.get("group_id", 0)
            from yuubot.core.models import GroupSetting
            setting = await GroupSetting.filter(group_id=gid).first()
            if setting:
                if not setting.bot_enabled:
                    return False
                if setting.response_mode == "free":
                    return True

            bot_qq = str(self.config.bot.qq)
            for seg in event.get("message", []):
                if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == bot_qq:
                    return True
            return False

        return False

    def _agent_exists(self, name: str) -> bool:
        """Check if an agent is defined in CHARACTER_REGISTRY."""
        from yuubot.characters import CHARACTER_REGISTRY
        return name in CHARACTER_REGISTRY

    async def _is_free_mode(self, event: dict) -> bool:
        """Check if the current context is in free mode."""
        msg_type = event.get("message_type")
        if msg_type != "group":
            return False

        gid = event.get("group_id", 0)
        from yuubot.core.models import GroupSetting
        setting = await GroupSetting.filter(group_id=gid).first()
        if setting and setting.response_mode == "free":
            return True
        return False
