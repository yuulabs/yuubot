"""Minimal RFC2 rendering compatibility helpers."""

from __future__ import annotations

import attrs
from datetime import datetime

from yuubot.core.types import InboundMessage
from yuubot.rendering import render_message_xml


@attrs.define(frozen=True)
class RenderPolicy:
    include_raw_message: bool = True


@attrs.define(frozen=True)
class RenderContext:
    group_name: str = ""
    bot_name: str = ""
    bot_qq: str = ""
    docker_host_mount: str = ""


async def render_signal(
    msg: InboundMessage,
    policy: RenderPolicy | None = None,
    context: RenderContext | None = None,
    *,
    upto_row_id: int = 0,
) -> str:
    del policy, context, upto_row_id
    display_name = msg.sender.card or msg.sender.nickname or str(msg.sender.user_id)
    msg_xml = render_message_xml(
        uid=msg.sender.user_id,
        name=msg.sender.nickname,
        display_name=display_name,
        time=msg.timestamp,
        segments=msg.segments,
        message_id=msg.message_id,
    )
    now = datetime.now().astimezone().strftime("现在是 %Y年%m月%d日 %H时%M分%S秒")
    return f"{now}\n{msg_xml}"
