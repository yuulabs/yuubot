"""Read this actor's recent conversations and user-visible text history.

Call ``await list_recents()`` without arguments. It returns a list of
``Conversation(id, title, history)`` objects owned by the current actor;
``history`` contains only ``ConversationMessage(kind="user"|"text", text=...)``
entries. Tool calls, developer messages, hidden metadata, and other actors'
conversations are excluded. Use this for preference/context recall, not as a
general conversation database.
"""

from __future__ import annotations

import os
from typing import Literal

import msgspec

from yb._daemon import daemon_url, request_json, request_json_value

ConversationMessageKind = Literal["user", "text"]


class ConversationMessage(msgspec.Struct, frozen=True):
    kind: ConversationMessageKind
    text: str


class Conversation(msgspec.Struct, frozen=True):
    id: str
    title: str
    history: list[ConversationMessage]


async def list_recents() -> list[Conversation]:
    """Return this actor's conversations with only user-visible text messages."""
    actor_id = os.getenv("YUUBOT_ACTOR_ID")
    if not actor_id:
        raise RuntimeError("YUUBOT_ACTOR_ID is required for yb.conversations")

    base_url = daemon_url()
    summaries = await request_json_value("GET", f"{base_url}/api/conversations")
    if not isinstance(summaries, list):
        raise RuntimeError("unexpected daemon API response")

    conversations: list[Conversation] = []
    for summary in summaries:
        if not isinstance(summary, dict) or summary.get("actor_id") != actor_id:
            continue
        conversation_id = summary.get("id")
        if not isinstance(conversation_id, str):
            continue
        history_payload = await request_json(
            "GET",
            f"{base_url}/api/conversations/{conversation_id}/history",
        )
        conversations.append(
            Conversation(
                conversation_id,
                _string_value(summary.get("title")),
                _visible_history(history_payload.get("items")),
            )
        )
    return conversations


def _visible_history(value: object) -> list[ConversationMessage]:
    if not isinstance(value, list):
        return []
    result: list[ConversationMessage] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind == "input" and payload.get("role") == "user":
            text = _input_text(payload.get("content"))
            if text:
                result.append(ConversationMessage("user", text))
        elif kind == "gen_text" and isinstance(payload.get("text"), str):
            result.append(ConversationMessage("text", payload["text"]))
    return result


def _input_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(
        item.get("text", "")
        for item in value
        if isinstance(item, dict) and item.get("kind") == "text" and isinstance(item.get("text"), str)
    )


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""
