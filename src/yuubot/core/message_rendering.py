"""Render gateway messages for actor runtimes."""

from __future__ import annotations

import yuullm

from yuubot.core.messages import IncomingMessage


def render_incoming_user_message(message: IncomingMessage) -> yuullm.Message:
    """Render an IncomingMessage as a yuullm user message for the LLM agent.

    Prepends a metadata prefix (sender identity) as a text item,
    followed by the message's content items directly.
    """
    prefix = message.render_metadata()
    items = message.content_items()
    return yuullm.user(prefix, *items)
