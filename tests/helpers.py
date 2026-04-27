"""Behavior-oriented test helpers."""

from __future__ import annotations

import asyncio
import json
import shlex


def build_im_send_argv(
    config_path: str,
    *,
    text: str,
    uid: int | None = None,
    gid: int | None = None,
) -> str:
    del config_path
    message = json.dumps([{"type": "text", "text": text}], ensure_ascii=False)
    parts = ["ybot", "im", "send"]
    if uid is not None:
        parts.extend(["--uid", str(uid)])
    if gid is not None:
        parts.extend(["--gid", str(gid)])
    command = " ".join(parts) + " -- " + shlex.quote(message)
    return json.dumps({"command": command}, ensure_ascii=False)


def sent_texts(sent: list[dict]) -> list[str]:
    """Extract text segments from captured recorder_api send_msg bodies."""
    texts: list[str] = []
    for body in sent:
        for seg in body.get("message", []):
            if seg.get("type") == "text":
                texts.append(seg.get("data", {}).get("text", ""))
    return texts


def llm_system_prompt(calls: list) -> str:
    """Extract concatenated system role text from the first LLM call."""
    if not calls:
        return ""
    for msg in calls[0].get("messages", []):
        if msg.get("role") == "system":
            content = msg.get("content", [])
            return "\n".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            )
    return ""


def llm_user_texts(calls: list) -> list[str]:
    """Extract all user-role text from the first LLM call."""
    if not calls:
        return []
    texts: list[str] = []
    for msg in calls[0].get("messages", []):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            texts.append("\n".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            ))
    return texts


def history_text(history: list) -> str:
    return "\n".join(str(item) for item in history)


async def wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)
