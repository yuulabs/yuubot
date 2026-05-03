"""Message functions for QQ contexts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict, cast

from tortoise.queryset import QuerySet

from yuubot.agent_fns.local import current_ctx_id, ensure_db_ready, is_master, local_config, service_payload
from yuubot.core.models import Context, ForwardRecord, MessageRecord
from yuubot.rendering import MessageList
from yuubot.services.im import ImService

__all__ = [
    "send_message",
    "ensure_ready",
    "message_records",
    "recent_messages",
    "search_messages",
    "read_forward",
    "list_contacts",
    "react_message",
    "send_file",
    "Context",
    "ForwardRecord",
    "MessageRecord",
]


class TextContentBlock(TypedDict):
    type: Literal["text"]
    text: str


class ImageUrlContent(TypedDict):
    url: str


class ImageContentBlock(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrlContent


ContentBlock = TextContentBlock | ImageContentBlock
MessageContent = str | list[ContentBlock]


class MessageSegment(TypedDict, total=False):
    type: str
    data: dict[str, Any]


class SendMessageResult(TypedDict):
    status: Literal["sent"]
    ctx_id: int
    message_type: Literal["group", "private"]
    target_id: int
    segments: list[MessageSegment]
    recorder: Any


class ForwardNode(TypedDict, total=False):
    user_id: int | str
    nickname: str
    time: int | str
    content: str
    message: Any
    segments: list[MessageSegment]


class ContactList(TypedDict):
    groups: list[dict[str, Any]]
    friends: list[dict[str, Any]]
    contexts: Any


class ReactMessageResult(TypedDict):
    status: Literal["reacted"]
    message_id: int
    emoji_id: str


class SendFileResult(TypedDict):
    status: Literal["sent"]
    ctx_id: int
    chat_type: Literal["group", "private"]
    target_id: int
    file: str
    name: str
    napcat: Any


async def ensure_ready() -> None:
    """Initialize worker-local ORM access to Yuubot's SQLite database."""
    await ensure_db_ready()


async def message_records(
    *,
    ctx_id: int | None = None,
    since: datetime | str | None = None,
    until: datetime | str | None = None,
    limit: int = 200,
) -> QuerySet[MessageRecord]:
    """Return a Tortoise QuerySet[MessageRecord] for local message queries.

    MessageRecord fields: id, message_id, ctx_id, user_id, nickname,
    display_name, content, raw_message, timestamp, media_files.

    Group agents are scoped to their current ctx_id. Master agents may pass
    another ctx_id or omit ctx_id, then chain arbitrary ORM filters:

        qs = await im.message_records(limit=500)
        rows = await qs.filter(content__icontains="猫").order_by("-timestamp")
    """
    await ensure_db_ready()
    qs = MessageRecord.all()
    if ctx_id is not None:
        qs = qs.filter(ctx_id=current_ctx_id(ctx_id))
    elif not is_master():
        qs = qs.filter(ctx_id=current_ctx_id(None))
    if since is not None:
        qs = qs.filter(timestamp__gte=since)
    if until is not None:
        qs = qs.filter(timestamp__lte=until)
    return qs.order_by("-timestamp").limit(max(1, min(int(limit), 5000)))


async def send_message(
    content: MessageContent,
    *,
    ctx_id: int | None = None,
) -> SendMessageResult:
    """Send text and/or inline images to the current QQ chat; returns delivery target and recorder response.

    ``content`` follows the ``yuullm.Content`` schema — QQ messages are naturally
    mixed text/image, so the same format is used here:

    - ``str`` — plain text shorthand
    - ``list[dict]`` — one or more content blocks, each a dict with ``"type"``:
        - Text block:  ``{"type": "text", "text": "hello"}``
        - Image block: ``{"type": "image_url", "image_url": {"url": "<url>"}}``
          where ``<url>`` is a ``https://`` URL or a ``file:///abs/path`` for a
          local file on the bot host.
    By default, use this function to send images. Use send_file only if you want to "upload" an image. These two are slightly differently displayed in QQ.

    Returns:
        ``{"status": "sent", "ctx_id": int, "message_type": "group"|"private",
        "target_id": int, "segments": [...], "recorder": ...}``. ``ctx_id`` is
        the yuubot conversation context; ``target_id`` is the QQ group id or
        private user id that actually received the message.
    """
    await ensure_db_ready()
    return cast(
        SendMessageResult,
        await ImService(config=local_config()).send_message(service_payload(content=content, ctx_id=ctx_id)),
    )


async def recent_messages(*, limit: int = 30, ctx_id: int | None = None) -> MessageList:
    """Read recent messages from a QQ context as a printable MessageList of message dicts.

    Returns a ``MessageList`` — prints as formatted XML, also supports list/dict operations:

        print(await im.recent_messages(limit=10))   # prints all messages as XML

        msgs = await im.recent_messages()
        print(msgs)                                  # same
        mid = msgs[-1]["message_id"]                 # QQ message ID for reactions etc.
        from_user = [m for m in msgs if m["user_id"] == 12345]

    Each dict has: ``db_id`` (int, monotone cursor for pagination), ``message_id``,
    ``user_id``, ``nickname``, ``display_name``, ``timestamp``, ``ctx_id``,
    ``content`` (plain text), ``segments`` (structured), ``media_files``,
    ``rendered`` (XML string).
    """
    await ensure_db_ready()
    service = ImService()
    return MessageList(await service.recent_messages(service_payload(limit=limit, ctx_id=ctx_id)))


async def search_messages(
    query: str,
    *,
    limit: int = 20,
    ctx_id: int | None = None,
    filter_user_id: int | None = None,
    before_id: int | None = None,
) -> MessageList:
    """Search stored QQ messages by keywords and return a printable MessageList of matching message dicts.

    ``query`` is a space-separated list of keywords; each word is an independent
    search term matched against message text (OR logic).  Do NOT use field
    prefixes like ``from:`` — use ``filter_user_id`` to restrict by sender.

    To paginate, call repeatedly with ``before_id=msgs[-1]["db_id"]`` until
    fewer than ``limit`` results are returned.

        msgs = await im.search_messages("keyword", limit=20)
        print(msgs)
        # next page:
        msgs2 = await im.search_messages("keyword", limit=20, before_id=msgs[-1]["db_id"])

    Args:
        query: Space-separated keywords (OR match against message content).
        limit: Max results per call, 1–100 (default 20).
        ctx_id: Restrict to a specific group/private context.
        filter_user_id: Restrict to messages sent by this QQ user ID.
        before_id: Return only messages with db_id < this value (pagination cursor).
    """
    await ensure_db_ready()
    service = ImService()
    return MessageList(
        await service.search_messages(
            service_payload(
                query=query,
                limit=limit,
                ctx_id=ctx_id,
                filter_user_id=filter_user_id,
                before_id=before_id,
            )
        )
    )


async def read_forward(forward_id: str) -> list[ForwardNode]:
    """Read the nodes inside a QQ merged-forward message id returned in a message segment."""
    await ensure_db_ready()
    return cast(list[ForwardNode], await ImService().read_forward(service_payload(forward_id=forward_id)))


async def list_contacts() -> ContactList:
    """List QQ groups, friends, and yuubot contexts visible to the master actor."""
    return cast(ContactList, await ImService(config=local_config()).list_contacts(service_payload()))


async def react_message(message_id: int | str, emoji_id: int | str) -> ReactMessageResult:
    """Send a QQ emoji reaction to an existing message; emoji_id may be a numeric id or supported alias."""
    await ensure_db_ready()
    return cast(
        ReactMessageResult,
        await ImService(config=local_config()).react_message(
            service_payload(message_id=message_id, emoji_id=emoji_id)
        ),
    )


async def send_file(
    path: str,
    *,
    name: str | None = None,
    ctx_id: int | None = None,
) -> SendFileResult:
    """Upload a workspace file as a QQ group/private file and return the upload target metadata.

    - ``path`` — absolute path or workspace-relative path on the bot host,
      e.g. ``"/workspace/ctx-1/report.pdf"`` or ``"report.pdf"``.
      Note: you can only send files under your workspace.
    - ``name`` — display name shown in QQ; defaults to the filename.
    - ``ctx_id`` — target context; defaults to current.

    Returns:
        ``{"status": "sent", "ctx_id": int, "chat_type": "group"|"private",
        "target_id": int, "file": "/abs/path", "name": str, "napcat": ...}``.
    """
    await ensure_db_ready()
    return cast(
        SendFileResult,
        await ImService(config=local_config()).send_file(service_payload(path=path, name=name, ctx_id=ctx_id)),
    )
