"""Master/Group scope helpers."""

from __future__ import annotations

from typing import Literal

from yuubot.core.types import InboundMessage


BotKind = Literal["master", "group"]


def is_master_user(user_id: int, master_id: int) -> bool:
    """Return whether a QQ user is the configured Master."""

    return bool(master_id) and int(user_id) == int(master_id)


def bot_kind_for_chat(chat_type: str, user_id: int, master_id: int) -> BotKind:
    """Classify a message into the Master private bot or Group bot."""

    if chat_type == "private" and is_master_user(user_id, master_id):
        return "master"
    return "group"


def bot_kind_for_message(message: InboundMessage, master_id: int) -> BotKind:
    return bot_kind_for_chat(
        message.chat_type,
        message.sender.user_id,
        master_id,
    )
