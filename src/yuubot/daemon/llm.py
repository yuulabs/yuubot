"""LLM command executor — sends HumanMessage to Actor mailbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import attrs
import yuullm

from yuubot.auth import bot_kind_for_message
from yuubot.commands.tree import CommandRequest
from yuubot.config import Config
from yuubot.core import env
from yuubot.daemon.actor import HumanMessage
from yuubot.daemon.render import render_signal


def _resolve_character(bot_kind: str) -> str:
    if bot_kind == "master":
        return "shiori"
    return "yuu"


def _workspace_root(config: Config, ctx_id: int) -> str:
    root = str(config.yuuagents.get("workspace_root", "") or env.get(env.WORKSPACE_ROOT, ""))
    base = Path(root).expanduser() if root else Path.home() / ".yuubot" / "workspaces"
    return str(base / f"ctx-{ctx_id}")


@attrs.define
class LLMExecutor:
    config: Config
    master_actor: Any = None  # YuubotActor, avoids circular import
    group_actor: Any = None   # YuubotActor, avoids circular import
    routing_engine: Any = None  # RoutingEngine | None

    async def __call__(self, request: CommandRequest) -> str | None:
        message = request.message
        bot_kind = bot_kind_for_message(message, self.config.bot.master)

        content = yuullm.user(await render_signal(message))

        # Determine character via RoutingEngine if available, else legacy fallback
        if self.routing_engine is not None:
            character_name = await self.routing_engine.select_actor(message)
        else:
            character_name = _resolve_character(bot_kind)

        # Build HumanMessage with session context
        human_msg = HumanMessage(
            content=content,
            ctx_id=message.ctx_id,
            chat_type=message.chat_type,
            sender_id=message.sender.user_id,
            character_name=character_name,
            reply_target=(
                str(message.group_id)
                if message.chat_type == "group"
                else str(message.sender.user_id)
            ),
            group_id=message.group_id,
            bot_kind=bot_kind,
            workspace_root=_workspace_root(self.config, message.ctx_id),
        )

        actor = self.master_actor if bot_kind == "master" else self.group_actor
        await actor.stage.mailbox.send(human_msg)
        return None  # reply handled asynchronously by Actor
