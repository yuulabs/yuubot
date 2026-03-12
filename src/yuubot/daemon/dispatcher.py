"""Message dispatcher — thin router: parse → permission → execute or queue."""

import asyncio

import httpx

from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import RootCommand, MatchResult
from yuubot.config import Config
from yuubot.core.models import Role, ReplySegment, AtSegment, segments_to_plain
from yuubot.core.onebot import parse_segments, build_send_msg, to_inbound_message
from yuubot.core.types import CommandRoute, ConversationRoute
from yuubot.daemon.routing import resolve_route
from yuubot.daemon.conversation import ConversationManager, conversation_worth_curating

from loguru import logger


def _ctx_key(event: dict) -> str:
    """Derive a context key from an event for per-ctx queuing."""
    msg_type = event.get("message_type", "private")
    if msg_type == "group":
        return f"group:{event.get('group_id', 0)}"
    return f"private:{event.get('user_id', 0)}"


class _CtxWorker:
    """Per-ctx sequential worker. One context, one agent at a time."""

    def __init__(self, key: str, dispatcher: "Dispatcher") -> None:
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
                logger.exception("Worker loop error in ctx={}", self.key)


def _group_batch(batch: list) -> list[list]:
    """Group consecutive items with the same match prefix for debouncing."""
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
        conv_mgr: ConversationManager | None = None,
    ) -> None:
        self.config = config
        self.root = root
        self.role_mgr = role_mgr
        self.deps = deps
        self.agent_runner = agent_runner
        self.conv_mgr = conv_mgr or ConversationManager()
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
        for expired in self.conv_mgr.collect_expired():
            if conversation_worth_curating(expired):
                asyncio.create_task(
                    self.agent_runner.curate(expired.history, expired.ctx_id, expired.user_id)
                )

        user_id = event.get("user_id", 0)
        group_id = event.get("group_id", 0)
        msg_type = event.get("message_type", "unknown")
        ctx_id = event.get("ctx_id", 0)

        logger.debug("event: type={} user={} group={} ctx={}", msg_type, user_id, group_id, ctx_id)

        if user_id == self.config.bot.qq:
            return

        # Route the message using pure routing logic
        inbound = to_inbound_message(event)
        route = resolve_route(
            inbound,
            self.root,
            has_active_conversation=lambda cid: self.conv_mgr.get(cid) is not None,
            is_auto=self.conv_mgr.is_auto,
            bot_qq=self.config.bot.qq,
        )

        if route is None:
            return

        # Convert route back to MatchResult for existing executor flow (transition)
        if isinstance(route, ConversationRoute):
            llm_cmd = self.root.find(["llm"])
            cmd_match = MatchResult(
                command=llm_cmd,
                remaining=route.text,
                entry="@" if not self.conv_mgr.is_auto(ctx_id) else "auto",
            )
        else:
            # CommandRoute → re-match to get the Command object (transition shim)
            segments = parse_segments(event.get("message", []))
            bot_qq_str = str(self.config.bot.qq)
            filtered = [
                s for s in segments
                if not isinstance(s, ReplySegment)
                and not (isinstance(s, AtSegment) and s.qq == bot_qq_str)
            ]
            replies = [s for s in segments if isinstance(s, ReplySegment)]
            plain = segments_to_plain(filtered + replies).strip()
            cmd_match = self.root.match_message(plain)
            if cmd_match is None:
                return

        should_respond = await self._should_respond(event)
        logger.info(
            "should_respond: user={} group={} type={} result={}",
            user_id, group_id, msg_type, should_respond,
        )
        if not should_respond:
            return

        scope = str(event.get("group_id", "global"))
        role = await self.role_mgr.get(event["user_id"], scope)
        if not cmd_match.command.check_permission(role):
            logger.info(
                "Permission denied: user={} role={} cmd={}",
                user_id, role, cmd_match.command.prefix,
            )
            return

        logger.info("Command accepted: user={} cmd={}", user_id, cmd_match.command.prefix)

        if cmd_match.command.interactive:
            await self._enqueue_or_pending(cmd_match, event)
        else:
            try:
                reply = await cmd_match.command.executor(cmd_match.remaining, event, self.deps)
                if reply:
                    await self._send_reply(event, reply)
            except Exception:
                logger.exception("Error executing command: {}", cmd_match.command.prefix)

    def _enqueue(self, match, event: dict) -> None:
        """Put a (match, event) pair into the per-ctx worker queue."""
        key = _ctx_key(event)
        if key not in self._workers:
            w = _CtxWorker(key, self)
            self._workers[key] = w
            w.start()
        self._workers[key].queue.put_nowait((match, event))

    async def _enqueue_or_pending(self, match: MatchResult, event: dict) -> None:
        """If conversation is running, enqueue as pending; otherwise enqueue for worker."""
        ctx_id = event.get("ctx_id", 0)
        conv = self.conv_mgr.get(ctx_id)
        if conv is not None and conv.state == "running":
            payload = await self._build_pending_payload(event)
            self.conv_mgr.enqueue_pending(ctx_id, payload)
            conv_obj = self.conv_mgr.get(ctx_id)
            if conv_obj:
                self.conv_mgr.touch(conv_obj)
            logger.info("Enqueued pending message for running conv ctx={}", ctx_id)
            return
        self._enqueue(match, event)

    async def _build_pending_payload(self, event: dict) -> str:
        """Build XML payload for a pending message."""
        from datetime import datetime, timezone
        from yuubot.core.models import AtSegment as _AtSeg, segments_to_json
        from yuubot.skills.im.formatter import format_message_to_xml, get_user_alias

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

    async def _handle(self, match: MatchResult | None, event: dict) -> None:
        """Execute a queued command. Always called from within a _CtxWorker."""
        if match is None or match.command.executor is None:
            return
        reply = await match.command.executor(match.remaining, event, self.deps)
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
            return False
        if user_id == self.config.bot.master:
            return True

        # Active session: respond to continue it
        ctx_id = event.get("ctx_id", 0)
        if ctx_id and self.conv_mgr.get(ctx_id) is not None:
            if msg_type == "private":
                return True
            if msg_type == "group":
                bot_qq = str(self.config.bot.qq)
                for seg in event.get("message", []):
                    if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == bot_qq:
                        return True

        if msg_type == "private":
            return user_id in self.config.response.dm_whitelist

        if msg_type == "group":
            gid = event.get("group_id", 0)
            from yuubot.core.models import GroupSetting
            try:
                setting = await GroupSetting.filter(group_id=gid).first()
            except Exception as e:
                from tortoise import Tortoise
                ctx = Tortoise._get_context()
                logger.error(
                    "DB query failed | ctx={} inited={} conn={}",
                    id(ctx) if ctx else None,
                    ctx.inited if ctx else None,
                    id(ctx._connections) if ctx and ctx._connections else None,
                )
                raise
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
