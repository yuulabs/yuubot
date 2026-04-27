"""Minimal RFC2 rendering compatibility helpers."""

from __future__ import annotations

import attrs

from yuubot.core.models import segments_to_plain
from yuubot.core.types import InboundMessage


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
    return (segments_to_plain(msg.segments) or msg.raw_message).strip()
