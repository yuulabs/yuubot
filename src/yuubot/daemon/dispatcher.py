"""Message dispatcher — thin router: ingress → route → execute."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import Command, CommandRequest, RootCommand
from yuubot.config import Config
from yuubot.core.models import AtSegment
from yuubot.core.onebot import build_send_msg, to_inbound_message
from yuubot.core.types import ConversationRoute, InboundMessage, Route
from yuubot.daemon.routing import resolve_route
from yuubot.daemon.conversation import ConversationManager, conversation_worth_curating

from loguru import logger


def _ctx_key(message: InboundMessage) -> str:
    """Derive a context key from an inbound message for per-ctx queuing."""
    if message.chat_type == "group":
        return f"group:{message.group_id}"
    return f"private:{message.sender.user_id}"


class RoutedCommand:
    def __init__(
        self,
        *,
        route: Route,
        message: InboundMessage,
        command: Command,
        execute: Callable[[], Awaitable[str | None]],
    ) -> None:
        self.route = route
        self.message = message
        self.command = command
        self.execute = execute


def _mentions_bot(message: InboundMessage, bot_qq: int) -> bool:
    return any(isinstance(seg, AtSegment) and seg.qq == str(bot_qq) for seg in message.segments)


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
                first: RoutedCommand = await self.queue.get()
                # Drain queued items to debounce consecutive continuations
                batch = [first]
                while not self.queue.empty():
                    batch.append(self.queue.get_nowait())

                groups = _group_batch(batch)
                for group in groups:
                    item = group[0]
                    if isinstance(item.route, ConversationRoute) and len(group) > 1:
                        item.message.extra_messages.extend(peer.message for peer in group[1:])
                    try:
                        await self.dispatcher._handle(item)
                    except Exception:
                        logger.exception("Error handling command in ctx={}", self.key)

                for _ in batch:
                    self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop error in ctx={}", self.key)


def _group_batch(batch: list[RoutedCommand]) -> list[list[RoutedCommand]]:
    """Group consecutive items with the same match prefix for debouncing."""
    groups: list[list[RoutedCommand]] = []
    current = [batch[0]]
    for item in batch[1:]:
        prev = current[0]
        if (
            isinstance(item.route, ConversationRoute)
            and isinstance(prev.route, ConversationRoute)
            and item.command.prefix == prev.command.prefix
        ):
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
        self._group_settings_cache: dict[int, object] = {}  # group_id → GroupSetting
        self._group_settings_loaded_at: float = 0.0
        self._group_settings_ttl: float = 60.0  # seconds

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
                    self.agent_runner.curate(expired.history, expired.ctx_id, expired.started_by)
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

        routed = self._build_routed_command(route, inbound)
        if routed is None:
            return

        should_respond = await self._should_respond(inbound)
        logger.info(
            "should_respond: user={} group={} type={} result={}",
            user_id, group_id, msg_type, should_respond,
        )
        if not should_respond:
            return

        scope = str(inbound.group_id or "global")
        role = await self.role_mgr.get(inbound.sender.user_id, scope)
        if not routed.command.check_permission(role):
            logger.info(
                "Permission denied: user={} role={} cmd={}",
                user_id, role, routed.command.prefix,
            )
            return

        logger.info("Command accepted: user={} cmd={}", user_id, routed.command.prefix)

        if routed.command.interactive:
            await self._enqueue_or_pending(routed)
        else:
            try:
                reply = await routed.execute()
                if reply:
                    await self._send_reply(inbound, reply)
            except Exception:
                logger.exception("Error executing command: {}", routed.command.prefix)

    def _build_routed_command(self, route: Route, message: InboundMessage) -> RoutedCommand | None:
        if isinstance(route, ConversationRoute):
            command = self.root.find(["llm"])
            if command is None or command.executor is None:
                return None
            return RoutedCommand(
                route=route,
                message=message,
                command=command,
                execute=lambda: command.executor(
                    CommandRequest(
                        remaining=route.text,
                        message=message,
                        deps=self.deps,
                        command_path=("llm",),
                        entry="@"
                    )
                ),
            )

        command = self.root.find(list(route.command_path))
        if command is None or command.executor is None:
            return None
        return RoutedCommand(
            route=route,
            message=message,
            command=command,
            execute=lambda: command.executor(
                CommandRequest(
                    remaining=route.remaining,
                    message=message,
                    deps=self.deps,
                    command_path=route.command_path,
                    entry=route.entry,
                )
            ),
        )

    def _enqueue(self, routed: RoutedCommand) -> None:
        """Put a routed command into the per-ctx worker queue."""
        key = _ctx_key(routed.message)
        if key not in self._workers:
            w = _CtxWorker(key, self)
            self._workers[key] = w
            w.start()
        self._workers[key].queue.put_nowait(routed)

    async def _enqueue_or_pending(self, routed: RoutedCommand) -> None:
        """If conversation is running, enqueue as signal; otherwise enqueue for worker."""
        ctx_id = routed.message.ctx_id
        conv = self.conv_mgr.get(ctx_id)
        if conv is not None and conv.state == "running":
            active = self.agent_runner.get_active_run(ctx_id)
            if active is not None:
                rendered = await self.agent_runner.render_signal(routed.message)
                if rendered:
                    self.agent_runner.enqueue_signal(active.runtime_id, rendered)
            self.conv_mgr.touch(conv)
            logger.info("Enqueued signal for running conv ctx={}", ctx_id)
            return
        self._enqueue(routed)

    async def _handle(self, routed: RoutedCommand) -> None:
        """Execute a queued command. Always called from within a _CtxWorker."""
        reply = await routed.execute()
        if reply:
            await self._send_reply(routed.message, reply)

    async def _send_reply(self, message: InboundMessage, text: str) -> None:
        """Send a text reply back to the source context."""
        from yuubot.core.models import Message, TextSegment
        segments: Message = [TextSegment(text=text)]
        target_id = message.group_id if message.chat_type == "group" else message.sender.user_id
        body = build_send_msg(message.chat_type, target_id, segments)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{self.config.daemon.recorder_api}/send_msg", json=body)
        except Exception:
            logger.exception("Failed to send reply")

    async def _should_respond(self, message: InboundMessage) -> bool:
        """Check response mode (at/free) and DM whitelist."""
        msg_type = message.chat_type
        user_id = message.sender.user_id

        if user_id == self.config.bot.qq:
            return False
        if user_id == self.config.bot.master:
            return True

        # Active session: respond to continue it
        ctx_id = message.ctx_id
        if ctx_id and self.conv_mgr.get(ctx_id) is not None:
            if msg_type == "private":
                return True
            # Fall through to normal group logic below (handles bot_enabled + free mode)

        if msg_type == "private":
            return user_id in self.config.response.dm_whitelist

        if msg_type == "group":
            gid = message.group_id
            setting = await self._get_group_setting(gid)
            if setting:
                if not setting.bot_enabled:
                    return False
                if setting.response_mode == "free":
                    return True

            return _mentions_bot(message, self.config.bot.qq)

        return False

    def invalidate_group_settings_cache(self) -> None:
        """Force next _get_group_setting call to reload from DB."""
        self._group_settings_loaded_at = 0.0

    async def _get_group_setting(self, group_id: int):
        """Return cached GroupSetting for a group, refreshing if TTL expired."""
        now = time.monotonic()
        if now - self._group_settings_loaded_at > self._group_settings_ttl:
            await self._reload_group_settings()
        return self._group_settings_cache.get(group_id)

    async def _reload_group_settings(self) -> None:
        """Load all GroupSettings into the in-memory cache."""
        from yuubot.core.models import GroupSetting

        try:
            settings = await GroupSetting.all()
            self._group_settings_cache = {s.group_id: s for s in settings}
            self._group_settings_loaded_at = time.monotonic()
        except Exception:
            logger.exception("Failed to reload GroupSettings cache")
