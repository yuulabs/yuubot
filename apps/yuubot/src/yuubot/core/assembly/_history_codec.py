"""Encode / decode ``yuullm.PromptItem`` history items for persistence.

Persistence is plain JSON; this module is pure serialization, not
encryption. History content is stored as-is in ``item_json``.

Item kinds:

* ``"tools"`` — a ``yuullm.ToolSpecs`` item, JSON shape
  ``{"type": "tools", "tools": [...]}``.
* ``"message"`` — a ``yuullm.Message`` item, JSON shape
  ``{"type": "message", "role": "system"|"user"|"assistant"|"tool", "content": [...], "provider_extra": {}}``.

All content-item shapes ``yuullm`` supports (text, image_url, input_audio,
file, tool_call, tool_result, thinking, redacted_thinking) round-trip
structurally — encode stores them as-is inside ``content``, decode
reconstructs the ``yuullm.Message`` struct.

Binary content (image / audio / file payloads carried inline as base64 data
URIs) round-trips structurally via ``item_json`` in this phase. A dedicated
attachment store that swaps inline binary for filesystem path references +
rotation is a follow-up phase, out of scope here.
"""

from __future__ import annotations

import msgspec
import yuullm

ITEM_KIND_TOOLS = "tools"
ITEM_KIND_MESSAGE = "message"


def encode_prompt_item(item: yuullm.PromptItem) -> tuple[str, str]:
    """Encode one ``yuullm.PromptItem`` to ``(item_kind, item_json)``.

    Raises :class:`TypeError` if the item is neither a ``yuullm.Message`` nor
    a ``yuullm.ToolSpecs``.
    """
    if isinstance(item, yuullm.ToolSpecs):
        payload = {"type": "tools", "tools": list(item.tools)}
        return ITEM_KIND_TOOLS, msgspec.json.encode(payload).decode("utf-8")
    if isinstance(item, yuullm.Message):
        payload = {
            "type": "message",
            "role": item.role,
            "content": msgspec.to_builtins(item.content),
            "provider_extra": msgspec.to_builtins(item.provider_extra),
        }
        return ITEM_KIND_MESSAGE, msgspec.json.encode(payload).decode("utf-8")
    raise TypeError(
        f"unsupported prompt item: {type(item).__name__}; "
        "expected yuullm.Message or yuullm.ToolSpecs"
    )


def decode_prompt_item(item_kind: str, item_json: str) -> yuullm.PromptItem:
    """Decode ``(item_kind, item_json)`` back into a ``yuullm.PromptItem``.

    Raises :class:`ValueError` on unsupported ``item_kind`` or malformed
    payload shape.
    """
    try:
        payload = msgspec.json.decode(item_json.encode("utf-8"))
    except msgspec.DecodeError as exc:
        raise ValueError(f"invalid item_json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"item_json must decode to a dict, got {type(payload).__name__}")

    if item_kind == ITEM_KIND_TOOLS:
        kind = payload.get("type")
        if kind != "tools":
            raise ValueError(
                f"tools item_json must have type='tools', got {kind!r}"
            )
        tools = payload.get("tools")
        if not isinstance(tools, list):
            raise ValueError(
                f"tools item_json 'tools' must be a list, got {type(tools).__name__}"
            )
        return yuullm.ToolSpecs(tools)

    if item_kind == ITEM_KIND_MESSAGE:
        kind = payload.get("type")
        if kind != "message":
            raise ValueError(
                f"message item_json must have type='message', got {kind!r}"
            )
        role = payload.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported message role: {role!r}")
        content = payload.get("content")
        if not isinstance(content, list):
            raise ValueError(
                f"message 'content' must be a list, got {type(content).__name__}"
            )
        provider_extra = payload.get("provider_extra")
        if provider_extra is None:
            provider_extra = {}
        if not isinstance(provider_extra, dict):
            raise ValueError(
                "provider_extra must be a dict, got "
                f"{type(provider_extra).__name__}"
            )
        return yuullm.Message(
            role=role,  # type: ignore[arg-type]
            content=content,  # type: ignore[arg-type]
            provider_extra=provider_extra,  # type: ignore[arg-type]
        )

    raise ValueError(
        f"unsupported item_kind: {item_kind!r}; "
        f"expected {ITEM_KIND_TOOLS!r} or {ITEM_KIND_MESSAGE!r}"
    )
