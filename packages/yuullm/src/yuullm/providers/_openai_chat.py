"""OpenAI-compatible chat-completion message conversion."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..types import Content, Message, ToolCallItem, is_thinking_item
from ._content import (
    content_blocks,
    content_items,
    split_assistant_items,
    tool_result_items,
)
from ._image_cache import image_url_for_provider


def convert_openai_chat_messages(
    messages: Sequence[Message], *, preserve_cache_control: bool = False
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.role in {"system", "user"}:
            items = content_items(message.content, role=message.role)
            result.append(
                {
                    "role": message.role,
                    "content": _openai_content_blocks(
                        items, preserve_cache_control=preserve_cache_control
                    ),
                    **message.provider_extra,
                }
            )
        elif message.role == "assistant":
            content, thinking_items, tool_calls = split_assistant_items(message.content)
            entry: dict[str, Any] = {
                "role": "assistant",
                **message.provider_extra,
            }
            if content:
                entry["content"] = _openai_content_blocks(
                    content, preserve_cache_control=preserve_cache_control
                )
            if tool_calls:
                entry["tool_calls"] = [openai_tool_call(item) for item in tool_calls]
            # DeepSeek: pass thinking text back as top-level reasoning_content field.
            # Redacted thinking items are skipped (no DeepSeek equivalent).
            if thinking_items:
                reasoning_text = "".join(
                    ti["thinking"] for ti in thinking_items if is_thinking_item(ti)
                )
                if reasoning_text:
                    entry["reasoning_content"] = reasoning_text
            result.append(entry)
        else:
            for item in tool_result_items(message.content):
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": item["tool_call_id"],
                        "content": openai_tool_result_content(
                            item["content"],
                            preserve_cache_control=preserve_cache_control,
                        ),
                        **message.provider_extra,
                    }
                )
    return result


def openai_tool_call(item: ToolCallItem) -> dict[str, Any]:
    return {
        "id": item["id"],
        "type": "function",
        "function": {
            "name": item["name"],
            "arguments": item["arguments"],
        },
    }


def openai_tool_result_content(
    content: str | Content, *, preserve_cache_control: bool
) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    return _openai_content_blocks(content, preserve_cache_control=preserve_cache_control)


def _openai_content_blocks(
    content: Content, *, preserve_cache_control: bool
) -> list[dict[str, Any]]:
    blocks = content_blocks(content, preserve_cache_control=preserve_cache_control)
    return [_normalize_openai_image_block(block) for block in blocks]


def _normalize_openai_image_block(block: dict[str, Any]) -> dict[str, Any]:
    if block.get("type") != "image_url":
        return block
    image_url = block.get("image_url")
    if not isinstance(image_url, dict):
        return block
    url = image_url.get("url")
    if not isinstance(url, str):
        return block
    normalized = dict(block)
    normalized["image_url"] = {**image_url, "url": image_url_for_provider(url)}
    return normalized
