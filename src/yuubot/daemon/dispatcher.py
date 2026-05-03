"""Message dispatcher — ingress → route → command/agent execution."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx
from loguru import logger

from yuubot.commands.tree import Command, CommandRequest, RootCommand
from yuubot.config import Config
from yuubot.core.models import AtSegment, Message, ReplySegment, TextSegment, segments_to_plain
from yuubot.core.onebot import build_send_msg, to_inbound_message
from yuubot.core.types import CommandRoute, InboundMessage, Route
from yuubot.daemon.routing import resolve_route


def _ctx_key(message: InboundMessage) -> str:
    return f"ctx:{message.ctx_id}"


def _mentions_bot(message: InboundMessage, bot_qq: int) -> bool:
    return any(isinstance(seg, AtSegment) and seg.qq == str(bot_qq) for seg in message.segments)


def _command_text(message: InboundMessage, bot_qq: int) -> str:
    replies = [seg for seg in message.segments if isinstance(seg, ReplySegment)]
    others = [
        seg
        for seg in message.segments
        if not isinstance(seg, ReplySegment)
        and not (isinstance(seg, AtSegment) and seg.qq == str(bot_qq))
    ]
    cmd_segs = [seg for seg in others if not isinstance(seg, AtSegment)]
    return segments_to_plain(cmd_segs + replies).strip()  # type: ignore[invalid-argument-type]


def _message_preview(message: InboundMessage, max_chars: int = 160) -> str:
    text = (message.raw_message or segments_to_plain(message.segments)).strip()
    text = " ".join(text.split())
    if not text:
        return "<empty>"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


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


class _CtxWorker:
    def __init__(self, key: str, dispatcher: Dispatcher) -> None:
        self.key = key
        self.dispatcher = dispatcher
        self.queue: asyncio.Queue[RoutedCommand] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

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
            routed = await self.queue.get()
            try:
                await self.dispatcher._wait_until_runnable(routed)
                await self.dispatcher._handle(routed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error handling command in ctx={}", self.key)
            finally:
                self.queue.task_done()


class Dispatcher:
    def __init__(
        self,
        config: Config,
        root: RootCommand,
        deps: dict,
        master_actor=None,
        group_actor=None,
    ) -> None:
        self.config = config
        self.root = root
        self.deps = deps
        self.master_actor = master_actor
        self.group_actor = group_actor
        self._workers: dict[str, _CtxWorker] = {}
        self._group_settings_cache: dict[int, object] = {}
        self._group_settings_loaded_at = 0.0
        self._group_settings_ttl = 60.0

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        for worker in list(self._workers.values()):
            await worker.stop()
        self._workers.clear()

    async def dispatch(self, event: dict) -> None:
        if event.get("post_type") != "message":
            return
        if event.get("user_id", 0) == self.config.bot.qq:
            return

        inbound = to_inbound_message(event)
        await self.dispatch_message(inbound)

    async def dispatch_message(self, inbound: InboundMessage) -> None:
        preview = _message_preview(inbound)
        logger.info(
            "Received: type={} user={} group={} ctx={} text={}",
            inbound.chat_type,
            inbound.sender.user_id,
            inbound.group_id,
            inbound.ctx_id,
            preview,
        )

        route = resolve_route(
            inbound,
            self.root,
            bot_qq=self.config.bot.qq,
            master_id=self.config.bot.master,
        )
        if route is None:
            route = await self._resolve_dynamic_entry_route(inbound)
        if route is None:
            return

        routed = self._build_routed_command(route, inbound)
        if routed is None:
            return

        if not routed.command.is_accessible_to(inbound, self.config.bot.master):
            logger.info(
                "Command denied by scope: ctx={} user={} cmd={}",
                inbound.ctx_id,
                inbound.sender.user_id,
                routed.command.prefix,
            )
            return

        if not _bypasses_response_policy(route) and not await self._should_handle_route(route, inbound):
            logger.info("Message ignored by response policy: ctx={} text={}", inbound.ctx_id, preview)
            return

        if routed.command.interactive:
            await self._enqueue_or_pending(routed)
            return

        try:
            reply = await routed.execute()
            if reply:
                await self._send_reply(inbound, reply)
        except Exception:
            logger.exception("Error executing command: {}", routed.command.prefix)

    async def _resolve_dynamic_entry_route(self, message: InboundMessage) -> CommandRoute | None:
        entry_mgr = self.deps.get("entry_mgr")
        if entry_mgr is None:
            return None
        cmd_text = _command_text(message, self.config.bot.qq)
        if not cmd_text.startswith("/"):
            return None
        parts = cmd_text.split(None, 1)
        entry = parts[0]
        remaining = parts[1] if len(parts) > 1 else ""
        try:
            route = await entry_mgr.get_route(entry, str(message.group_id or "global"))
        except Exception:
            logger.debug("Dynamic entry mapping unavailable")
            return None
        if not route:
            return None
        return CommandRoute(command_path=tuple(route.split()), remaining=remaining, entry=entry)

    def _build_routed_command(self, route: Route, message: InboundMessage) -> RoutedCommand | None:
        command = self.root.find(list(route.command_path))
        if command is None or command.executor is None:
            return None
        executor = command.executor
        return RoutedCommand(
            route=route,
            message=message,
            command=command,
            execute=lambda: executor(
                CommandRequest(
                    remaining=route.remaining,
                    message=message,
                    deps=self.deps,
                    command_path=route.command_path,
                    entry=route.entry,
                )
            ),
        )

    async def _enqueue_or_pending(self, routed: RoutedCommand) -> None:
        key = _ctx_key(routed.message)
        worker = self._workers.get(key)
        if worker is None:
            worker = _CtxWorker(key, self)
            self._workers[key] = worker
            worker.start()
        worker.queue.put_nowait(routed)

    async def _wait_until_runnable(self, routed: RoutedCommand) -> None:
        # Actor handles per-agent serialization via asyncio.Lock
        return

    async def _handle(self, routed: RoutedCommand) -> None:
        reply = await routed.execute()
        if reply:
            await self._send_reply(routed.message, reply)

    async def _send_reply(self, message: InboundMessage, text: str) -> None:
        segments: Message = [TextSegment(text=text)]
        target_id = message.group_id if message.chat_type == "group" else message.sender.user_id
        body = build_send_msg(message.chat_type, target_id, segments)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{self.config.daemon.recorder_api}/send_msg_guaranteed", json=body)
        except Exception:
            logger.exception("Failed to send command reply")

    async def _should_respond(self, message: InboundMessage) -> bool:
        if message.sender.user_id == self.config.bot.qq:
            return False
        if message.sender.user_id == self.config.bot.master:
            return True
        if message.chat_type == "private":
            return message.sender.user_id in self.config.response.dm_whitelist
        if message.chat_type == "group":
            setting = await self._get_group_setting(message.group_id)
            if setting is not None:
                if not setting.bot_enabled:
                    return False
            return _mentions_bot(message, self.config.bot.qq)
        return False

    async def _should_handle_route(self, route: Route, message: InboundMessage) -> bool:
        if _is_explicit_non_llm_command(route):
            return await self._should_handle_explicit_command(message)
        return await self._should_respond(message)

    async def _should_handle_explicit_command(self, message: InboundMessage) -> bool:
        if message.sender.user_id == self.config.bot.qq:
            return False
        if message.sender.user_id == self.config.bot.master:
            return True
        if message.chat_type == "private":
            return message.sender.user_id in self.config.response.dm_whitelist
        if message.chat_type == "group":
            setting = await self._get_group_setting(message.group_id)
            return setting is None or bool(setting.bot_enabled)
        return False

    def invalidate_group_settings_cache(self) -> None:
        self._group_settings_loaded_at = 0.0

    async def _get_group_setting(self, group_id: int):
        now = time.monotonic()
        if now - self._group_settings_loaded_at <= self._group_settings_ttl:
            return self._group_settings_cache.get(group_id)
        try:
            from yuubot.core.models import GroupSetting

            settings = await GroupSetting.all()
            self._group_settings_cache = {int(setting.group_id): setting for setting in settings}
            self._group_settings_loaded_at = now
        except Exception:
            logger.debug("Group settings unavailable; falling back to mention-only mode")
            self._group_settings_cache = {}
            self._group_settings_loaded_at = now
        return self._group_settings_cache.get(group_id)


def _bypasses_response_policy(route: Route) -> bool:
    return route.command_path == ("bot", "on")


def _is_explicit_non_llm_command(route: Route) -> bool:
    return bool(route.entry) and route.entry not in {"@", "master"}
