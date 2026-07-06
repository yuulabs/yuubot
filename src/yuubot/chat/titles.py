"""Conversation title helpers."""

from ..actor.prompt import user_visible_text
from ..domain.messages import InputMessage

DEFAULT_TITLE_MAX_LEN = 80


def title_from_user_message(message: InputMessage, *, max_len: int = DEFAULT_TITLE_MAX_LEN) -> str:
    text = " ".join(user_visible_text(message).split())
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3].rstrip() + "..."
