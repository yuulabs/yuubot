"""Message functions for QQ contexts."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy
from yuubot.rendering import MessageList

_p = _DaemonProxy()


async def send_message(
    content: str | list[dict],
    *,
    ctx_id: int | None = None,
) -> dict[str, Any]:
    """Send a message to the current QQ context or an explicitly permitted context.

    ``content`` follows the ``yuullm.Content`` schema — QQ messages are naturally
    mixed text/image, so the same format is used here:

    - ``str`` — plain text shorthand
    - ``list[dict]`` — one or more content blocks, each a dict with ``"type"``:
        - Text block:  ``{"type": "text", "text": "hello"}``
        - Image block: ``{"type": "image_url", "image_url": {"url": "<url>"}}``
          where ``<url>`` is a ``https://`` URL or a ``file:///abs/path`` for a
          local file on the bot host.
    """
    return await _p.call("im", "send_message", content=content, ctx_id=ctx_id)


async def recent_messages(*, limit: int = 30, ctx_id: int | None = None) -> MessageList:
    """Read recent messages from the current QQ context.

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
    return MessageList(await _p.call("im", "recent_messages", limit=limit, ctx_id=ctx_id))


async def search_messages(
    query: str,
    *,
    limit: int = 20,
    ctx_id: int | None = None,
    filter_user_id: int | None = None,
    before_id: int | None = None,
) -> MessageList:
    """Search messages by keywords.

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
    return MessageList(
        await _p.call(
            "im",
            "search_messages",
            query=query,
            limit=limit,
            ctx_id=ctx_id,
            filter_user_id=filter_user_id,
            before_id=before_id,
        )
    )


async def read_forward(forward_id: str) -> list[dict[str, Any]]:
    """Read a stored merged-forward message by forward id."""
    return await _p.call("im", "read_forward", forward_id=forward_id)


async def list_contacts() -> dict[str, Any]:
    """List contacts and groups visible to the current actor."""
    return await _p.call("im", "list_contacts")


async def react_message(message_id: int | str, emoji_id: int | str) -> dict[str, Any]:
    """React to a QQ message when the recorder and policy allow it."""
    return await _p.call("im", "react_message", message_id=message_id, emoji_id=emoji_id)


async def send_file(
    path: str,
    *,
    name: str | None = None,
    ctx_id: int | None = None,
) -> dict[str, Any]:
    """Upload a file to the current QQ context (group file space or private chat).

    - ``path`` — absolute path or workspace-relative path on the bot host,
      e.g. ``"/workspace/ctx-1/report.pdf"`` or ``"report.pdf"``.
    - ``name`` — display name shown in QQ; defaults to the filename.
    - ``ctx_id`` — target context; defaults to current.
    """
    return await _p.call("im", "send_file", path=path, name=name, ctx_id=ctx_id)
