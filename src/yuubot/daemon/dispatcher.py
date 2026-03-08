"""Message dispatcher — command parsing, permission check, agent trigger."""

import asyncio
import json
import logging
import re

import httpx

from yuubot.commands.builtin import _exec_llm
from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import RootCommand, MatchResult
from yuubot.config import Config
from yuubot.core.models import Role, ReplySegment, AtSegment, segments_to_plain
from yuubot.core.onebot import parse_segments, build_send_msg
from yuubot.daemon.session import SessionManager

log = logging.getLogger(__name__)

_AGENT_TAG_RE = re.compile(r"^#(\w+)\s*")


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
    """Per-ctx sequential worker. If a task exceeds *timeout* seconds,
    the next queued task starts without waiting for it to finish."""

    def __init__(self, key: str, dispatcher: Dispatcher, timeout: float) -> None:
        self.key = key
        self.dispatcher = dispatcher
        self.timeout = timeout
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
            match, event = await self.queue.get()
            try:
                handle = asyncio.create_task(self.dispatcher._handle(match, event))
                try:
                    await asyncio.wait_for(asyncio.shield(handle), timeout=self.timeout)
                except asyncio.TimeoutError:
                    log.warning("ctx=%s task exceeded %ss, moving on", self.key, self.timeout)
            except Exception:
                log.exception("Error handling command in ctx=%s", self.key)
            finally:
                self.queue.task_done()


class Dispatcher:
    """Receives events, matches commands, dispatches to executors or agent."""

    CTX_TIMEOUT: float = 120.0  # seconds before unblocking next task in same ctx

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
        for w in self._workers.values():
            await w.stop()
        self._workers.clear()

    async def dispatch(self, event: dict) -> None:
        """Called by WS client for each incoming event."""
        with open("/tmp/yuubot_dispatch.log", "a") as f:
            f.write(f"[DISPATCH] {json.dumps(event)}\n")
            f.flush()

        if event.get("post_type") != "message":
            return

        user_id = event.get("user_id", 0)
        group_id = event.get("group_id", 0)
        msg_type = event.get("message_type", "unknown")

        with open("/tmp/yuubot_dispatch.log", "a") as f:
            f.write(f"[MESSAGE] user={user_id} group={group_id} type={msg_type}\n")
            f.flush()

        # Check if we should respond
        should_respond = await self._should_respond(event)
        log.info("should_respond check: user=%s group=%s type=%s result=%s", user_id, group_id, msg_type, should_respond)
        if not should_respond:
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
        log.info("Message text: %s", plain)

        # Check for active session BEFORE command matching
        ctx_id = event.get("ctx_id", 0)
        session = self.session_mgr.get(ctx_id)
        if session is not None:
            # Try command match first to detect non-llm commands
            cmd_match = self.root.match_message(plain)
            is_llm_match = cmd_match is not None and cmd_match.command.executor is _exec_llm

            if cmd_match is not None and not is_llm_match:
                # Non-llm command (e.g. /close, /bot, /help) — close session
                self.session_mgr.close(ctx_id)
                log.info("Session closed by command: ctx=%s cmd=%s", ctx_id, cmd_match.command.prefix)
                event["_session_closed"] = True
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
                log.info("Session continuation: ctx=%s agent=%s", ctx_id, session.agent_name)
                event["_session"] = session
                event["_session_agent"] = session.agent_name
                event["_session_remaining"] = remaining
                self._enqueue(None, event)
                return
        else:
            # No active session — normal command matching
            match = self.root.match_message(plain)
            if match is None:
                log.info("No command match for: %s", plain)
                return

        log.info("Matched command: %s", match.command.prefix)

        # Permission check
        scope = str(event.get("group_id", "global"))
        role = await self.role_mgr.get(event["user_id"], scope)

        is_free_mode = await self._is_free_mode(event)
        is_llm_cmd = match.command.executor is _exec_llm

        log.info("Permission check: user=%s role=%s cmd=%s free_mode=%s is_llm=%s",
                 user_id, role, match.command.prefix, is_free_mode, is_llm_cmd)

        if not (is_free_mode and is_llm_cmd):
            if not match.command.check_permission(role):
                log.info("Permission denied: user=%s role=%s cmd=%s", event["user_id"], role, match.command.prefix)
                return

        log.info("Command accepted: user=%s cmd=%s", user_id, match.command.prefix)
        self._enqueue(match, event)

    def _enqueue(self, match, event: dict) -> None:
        """Put a (match, event) pair into the per-ctx worker queue."""
        key = _ctx_key(event)
        if key not in self._workers:
            w = _CtxWorker(key, self, self.CTX_TIMEOUT)
            self._workers[key] = w
            w.start()
        self._workers[key].queue.put_nowait((match, event))

    async def _handle(self, match, event: dict) -> None:
        # Session continuation — match is None, session info in event
        session = event.pop("_session", None)
        agent_name = event.pop("_session_agent", None)
        session_remaining = event.pop("_session_remaining", None)

        if session is not None and agent_name is not None:
            # Validate agent still exists
            agents_cfg = self.config.yuuagents.get("agents", {})
            if agent_name not in agents_cfg:
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
            self.session_mgr.update_history(session, history, tokens)
            return

        # Normal command handling
        if match is None:
            return

        executor = match.command.executor
        if executor is _exec_llm:
            # New /yllm invocation — parse #agent_name
            llm_agent_name, remaining = _parse_agent_tag(match.remaining)
            match.remaining = remaining

            agents_cfg = self.config.yuuagents.get("agents", {})
            if llm_agent_name not in agents_cfg:
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
            self.session_mgr.update_history(new_session, history, tokens)

        elif executor is not None:
            reply = await executor(match.remaining, event, self.deps)
            if reply:
                await self._send_reply(event, reply)

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
            log.exception("Failed to send reply")

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
