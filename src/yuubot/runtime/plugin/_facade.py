"""External plugin HTTP facade communication and inbound message types."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping

import msgspec

from yuubot.core.messages import IncomingMessage, MessageSource


class ExternalPluginInboundMessage(msgspec.Struct, forbid_unknown_fields=False):
    """A message received from an external plugin via /ingest."""

    integration_id: str
    message_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    kind: str = ""
    text: str = ""
    segments: list[dict[str, object]] = msgspec.field(default_factory=list)
    content: list[dict[str, object]] = msgspec.field(default_factory=list)
    source_path: str = ""
    timestamp: int = 0

    def to_message(self) -> IncomingMessage:
        content = self.content or self.segments or _text_content(self.text)
        fields: dict[str, object] = {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "kind": self.kind,
            "source": MessageSource(path=self.source_path),
            "content": content,
        }
        if self.timestamp:
            fields["timestamp"] = self.timestamp
        return msgspec.convert(fields, type=IncomingMessage, strict=False)


# ── HTTP helpers ────────────────────────────────────────────────────


async def post_json(
    url: str,
    *,
    token: str,
    payload: Mapping[str, object],
    timeout_s: float = 10.0,
) -> object:
    return await asyncio.to_thread(_post_json_sync, url, token, payload, timeout_s)


def _post_json_sync(
    url: str,
    token: str,
    payload: Mapping[str, object],
    timeout_s: float,
) -> object:
    data = json.dumps(dict(payload), ensure_ascii=True).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"plugin facade returned HTTP {exc.code}: {detail}") from exc
    if not raw:
        return None
    return json.loads(raw.decode())


# ── Internal ────────────────────────────────────────────────────────


def _text_content(text: str) -> list[dict[str, object]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]
