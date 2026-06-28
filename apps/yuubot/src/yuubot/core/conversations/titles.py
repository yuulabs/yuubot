"""Conversation title derivation."""

from __future__ import annotations

import yuullm


def _conversation_title_from_first_turn(
    user_message: yuullm.Message,
    assistant_message: yuullm.Message,
) -> str:
    user_text = yuullm.render_message_text(user_message).strip()
    assistant_text = yuullm.render_message_text(assistant_message).strip()
    text = user_text or assistant_text
    if not text:
        return ""
    title = " ".join(text.split())
    if len(title) <= 80:
        return title
    return title[:77].rstrip() + "..."

