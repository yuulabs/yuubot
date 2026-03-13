"""Behavior-oriented test helpers."""

from __future__ import annotations

import json
import shlex


def build_im_send_argv(
    config_path: str,
    *,
    text: str,
    uid: int | None = None,
    gid: int | None = None,
) -> str:
    """Build an `execute_skill_cli` command for a real `ybot im send` call."""
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


def history_text(history: list) -> str:
    """Flatten session history into a string for behavior assertions."""
    return "\n".join(str(item) for item in history)
