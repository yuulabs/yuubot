"""Data models for messages, events, and contexts.

Wire-format models use msgspec.Struct.
Database models use tortoise.Model.
"""

import html as _html
from typing import Literal

import json as _json

import msgspec
from tortoise import fields
from tortoise.models import Model


# ── Message Segments ──────────────────────────────────────────────


class TextSegment(msgspec.Struct, tag="text"):
    text: str


class ImageSegment(msgspec.Struct, tag="image"):
    url: str = ""
    file: str = ""
    local_path: str = ""


class AtSegment(msgspec.Struct, tag="at"):
    qq: str
    name: str = ""


class ReplySegment(msgspec.Struct, tag="reply"):
    id: str
    content: str = ""


class ForwardSegment(msgspec.Struct, tag="forward"):
    id: str
    summary: str = ""


class JsonSegment(msgspec.Struct, tag="json"):
    data: str  # raw JSON string from QQ's json card


class PokeSegment(msgspec.Struct, tag="poke"):
    sender_qq: str       # who initiated the poke
    target_qq: str       # who got poked
    action: str = "戳了戳"  # customized verb (戳了戳, 踢了踢, etc.)
    suffix: str = ""     # optional suffix text


class ReactSegment(msgspec.Struct, tag="react"):
    message_id: str
    emoji_id: str


Segment = (
    TextSegment
    | ImageSegment
    | AtSegment
    | ReplySegment
    | ForwardSegment
    | JsonSegment
    | PokeSegment
    | ReactSegment
)

Message = list[Segment]


def _json_card_plain(data: str) -> str:
    """Extract readable text from a QQ JSON card for FTS indexing."""
    try:
        obj = _json.loads(data)
        meta = obj.get("meta", {})
        # Try common card layouts
        for key in meta:
            block = meta[key]
            if isinstance(block, dict):
                title = block.get("title", "")
                desc = block.get("desc", "")
                if title:
                    return f"[卡片:{title}]" if not desc else f"[卡片:{title} - {desc}]"
        # Fallback: prompt field
        prompt = obj.get("prompt", "")
        if prompt:
            return f"[卡片:{prompt}]"
    except Exception:
        pass
    return "[卡片]"


def segments_to_plain(segments: Message) -> str:
    """Extract plain text from message segments."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSegment):
            parts.append(seg.text)
        elif isinstance(seg, AtSegment):
            parts.append(f"@{seg.name or seg.qq}")
        elif isinstance(seg, ImageSegment):
            parts.append("[图片]")
        elif isinstance(seg, ReplySegment):
            parts.append(f"[引用:{seg.id} {seg.content}]" if seg.content else f"[引用:{seg.id}]")
        elif isinstance(seg, ForwardSegment):
            summary = f":{seg.summary}" if seg.summary else ""
            parts.append(f"[合并转发:{seg.id}{summary}]")
        elif isinstance(seg, JsonSegment):
            parts.append(_json_card_plain(seg.data))
        elif isinstance(seg, PokeSegment):
            suffix = f" {seg.suffix}" if seg.suffix else ""
            parts.append(f"[{seg.sender_qq} {seg.action} {seg.target_qq}{suffix}]")
        elif isinstance(seg, ReactSegment):
            parts.append(f"[表情回应:{seg.emoji_id} -> 消息{seg.message_id}]")
    return "".join(parts)


def _image_content_text(seg: ImageSegment) -> str:
    ref = seg.local_path or seg.url or seg.file
    return f"[图片:{ref}]" if ref else "[图片]"


def _img_src(seg: ImageSegment) -> str:
    if seg.local_path:
        return f"file://{seg.local_path}"
    if seg.file.startswith("/") or seg.file.startswith("file://"):
        return seg.file if seg.file.startswith("file://") else f"file://{seg.file}"
    return seg.url or seg.file


def segments_to_xml_body(segments: Message) -> str:
    """Render message segments as XML body text.

    Text is XML-escaped. Images become ``<img src="file:///...">`` (local path
    preferred) or ``<img src="https://...">`` when only a CDN URL is available.
    Other segment types render as inline text, matching segments_to_plain().
    """
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSegment):
            parts.append(_html.escape(seg.text))
        elif isinstance(seg, ImageSegment):
            src = _img_src(seg)
            parts.append(f'<img src="{_html.escape(src, quote=True)}">' if src else "[图片]")
        elif isinstance(seg, AtSegment):
            parts.append(f"@{seg.name or seg.qq}")
        elif isinstance(seg, ReplySegment):
            if seg.content:
                parts.append(f'<quote message_id="{_html.escape(seg.id, quote=True)}">{_html.escape(seg.content)}</quote>')
            else:
                parts.append(f"[引用:{seg.id}]")
        elif isinstance(seg, ForwardSegment):
            summary = f":{seg.summary}" if seg.summary else ""
            parts.append(f"[合并转发:{seg.id}{summary}]")
        elif isinstance(seg, JsonSegment):
            parts.append(_json_card_plain(seg.data))
        elif isinstance(seg, PokeSegment):
            suffix = f" {seg.suffix}" if seg.suffix else ""
            parts.append(f"[{seg.sender_qq} {seg.action} {seg.target_qq}{suffix}]")
        elif isinstance(seg, ReactSegment):
            parts.append(f"[表情回应:{seg.emoji_id} -> 消息{seg.message_id}]")
    return "".join(parts)


def segments_to_content(segments: Message, *, include_images: bool = True) -> list:
    """Convert message segments to a yuullm Content list (multimodal content items).

    ImageSegments with a URL become ImageItems when include_images is true.
    Otherwise they fall back to text with a media reference so text-only models
    can still ask a vision helper to inspect the image.
    All other non-text segments are rendered as inline text, matching segments_to_plain().
    """
    import yuullm

    result: list = []
    text_buf: list[str] = []

    def flush() -> None:
        if text_buf:
            result.append(yuullm.TextItem(type="text", text="".join(text_buf)))
            text_buf.clear()

    for seg in segments:
        if isinstance(seg, TextSegment):
            text_buf.append(seg.text)
        elif isinstance(seg, ImageSegment):
            if include_images and seg.url:
                flush()
                result.append(yuullm.ImageItem(type="image_url", image_url={"url": seg.url}))
            else:
                text_buf.append(_image_content_text(seg))
        elif isinstance(seg, AtSegment):
            text_buf.append(f"@{seg.name or seg.qq}")
        elif isinstance(seg, ReplySegment):
            text_buf.append(f"[引用:{seg.id} {seg.content}]" if seg.content else f"[引用:{seg.id}]")
        elif isinstance(seg, ForwardSegment):
            summary = f":{seg.summary}" if seg.summary else ""
            text_buf.append(f"[合并转发:{seg.id}{summary}]")
        elif isinstance(seg, JsonSegment):
            text_buf.append(_json_card_plain(seg.data))
        elif isinstance(seg, PokeSegment):
            suffix = f" {seg.suffix}" if seg.suffix else ""
            text_buf.append(f"[{seg.sender_qq} {seg.action} {seg.target_qq}{suffix}]")
        elif isinstance(seg, ReactSegment):
            text_buf.append(f"[表情回应:{seg.emoji_id} -> 消息{seg.message_id}]")

    flush()
    return result


def segments_to_json(segments: Message) -> str:
    """Serialize segments to JSON string."""
    return msgspec.json.encode(segments).decode()


def segments_from_json(raw: str | bytes) -> Message:
    """Deserialize segments from JSON string."""
    return msgspec.json.decode(raw if isinstance(raw, bytes) else raw.encode(), type=list[Segment])


# ── OneBot V11 Event Models ──────────────────────────────────────


class MessageEvent(msgspec.Struct):
    post_type: Literal["message"]
    message_type: Literal["private", "group"]
    message_id: int
    user_id: int
    message: list[dict]  # raw OneBot CQ segments
    raw_message: str
    time: int
    self_id: int
    # group fields (optional)
    group_id: int = 0
    # sender info
    sender: dict = msgspec.field(default_factory=dict)

    @property
    def nickname(self) -> str:
        return self.sender.get("nickname", "")

    @property
    def display_name(self) -> str:
        """Group card (群名片) if available, else empty."""
        return self.sender.get("card", "")

    @property
    def target_id(self) -> int:
        return self.group_id if self.message_type == "group" else self.user_id

    @property
    def ctx_type(self) -> str:
        return self.message_type


class NoticeEvent(msgspec.Struct):
    post_type: Literal["notice"]
    notice_type: str
    time: int
    self_id: int
    user_id: int = 0
    group_id: int = 0


class MetaEvent(msgspec.Struct):
    post_type: Literal["meta_event"]
    meta_event_type: str
    time: int
    self_id: int


# ── Context ──────────────────────────────────────────────────────


class CtxInfo(msgspec.Struct):
    ctx_id: int
    type: str  # 'private' | 'group'
    target_id: int


# ── Tortoise ORM Models ─────────────────────────────────────────


class Context(Model):
    id = fields.IntField(primary_key=True)

    # ── Gateway identity ──
    channel = fields.CharField(max_length=32, default="qq")
    key = fields.CharField(max_length=256, default="")
    kind = fields.CharField(max_length=32, default="other")
    label = fields.CharField(max_length=256, default="")

    # ── Channel-specific attributes ──
    metadata = fields.JSONField(default=dict)

    # ── State ──
    last_message_at = fields.DatetimeField(null=True)
    archived = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    # ── Backward compat (QQ-only) ──
    target_id = fields.BigIntField(default=0)
    target_str = fields.CharField(max_length=256, default="")
    is_group = fields.BooleanField(default=False)
    is_private = fields.BooleanField(default=False)
    type = fields.CharField(max_length=16, default="")

    class Meta:
        table = "contexts"


class MessageRecord(Model):
    id = fields.IntField(primary_key=True)
    message_id = fields.BigIntField(null=True, db_index=True)
    ctx = fields.ForeignKeyField("models.Context", related_name="messages")
    user_id = fields.BigIntField()
    nickname = fields.CharField(max_length=64, null=True)
    display_name = fields.CharField(max_length=64, null=True)
    content = fields.TextField()
    raw_message = fields.TextField()
    timestamp = fields.DatetimeField()
    media_files = fields.JSONField(default=list)  # List of local file paths

    class Meta:
        table = "messages"


class ForwardRecord(Model):
    id = fields.IntField(primary_key=True)
    forward_id = fields.CharField(max_length=128, unique=True)
    summary = fields.CharField(max_length=256, default="")
    raw_nodes = fields.TextField()
    source_message_id = fields.BigIntField(null=True)
    source_ctx_id = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "forwards"


class Memory(Model):
    id = fields.IntField(primary_key=True)
    content = fields.TextField()
    ctx = fields.ForeignKeyField("models.Context", related_name="memories", null=True)
    scope = fields.CharField(max_length=16, default="private")  # "private" | "public"
    created_at = fields.DatetimeField(auto_now_add=True)
    last_accessed = fields.DatetimeField(auto_now_add=True)
    source_user_id = fields.BigIntField(null=True)
    trashed_at = fields.DatetimeField(null=True)  # set = trashed; None = active

    class Meta:
        table = "memories"


class MemoryTag(Model):
    id = fields.IntField(primary_key=True)
    memory = fields.ForeignKeyField("models.Memory", related_name="tags", on_delete=fields.CASCADE)
    tag = fields.CharField(max_length=64)

    class Meta:
        table = "memory_tags"
        unique_together = (("memory", "tag"),)


class MemoryRecallTerm(Model):
    """Semantic recall trigger for a memory.

    Recall terms are short strings (nicknames, meme fragments, jargon) that
    should trigger a memory during probe even if jieba would filter them out.
    Curator sets these based on "unexpectedness" — a common word with special
    meaning in context has high information value.
    """

    id = fields.IntField(primary_key=True)
    term = fields.CharField(max_length=50)
    memory = fields.ForeignKeyField(
        "models.Memory", related_name="recall_terms", on_delete=fields.CASCADE,
    )

    class Meta:
        table = "memory_recall_terms"
        unique_together = (("memory", "term"),)


class GroupSetting(Model):
    group_id = fields.BigIntField(primary_key=True)
    bot_enabled = fields.BooleanField(default=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "group_settings"


class EntryMapping(Model):
    id = fields.IntField(primary_key=True)
    entry = fields.CharField(max_length=64)
    route = fields.CharField(max_length=128)
    scope = fields.CharField(max_length=32)

    class Meta:
        table = "entry_mappings"
        unique_together = (("entry", "scope"),)


class MemoryConfigKV(Model):
    key = fields.CharField(max_length=64, primary_key=True)
    value = fields.TextField()

    class Meta:
        table = "memory_config"


class AdminConfigKV(Model):
    """Persisted admin config overrides — survives restart, layered on top of config.yaml."""

    key = fields.CharField(max_length=128, primary_key=True)
    value = fields.TextField()

    class Meta:
        table = "admin_config"


class ImageEntry(Model):
    id = fields.IntField(primary_key=True)
    local_path = fields.CharField(max_length=512, unique=True)
    description = fields.TextField(default="")
    tags = fields.JSONField(default=list)
    source_msg_id = fields.BigIntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "images"


class VisionCache(Model):
    """Persistent cache for vision describe results."""

    id = fields.IntField(primary_key=True)
    host_path = fields.CharField(max_length=512, unique=True)
    description = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "vision_cache"


class ScheduledTask(Model):
    id = fields.IntField(primary_key=True)
    cron = fields.CharField(max_length=128)
    task = fields.TextField()
    agent = fields.CharField(max_length=64)
    ctx_id = fields.IntField(null=True)
    created_by = fields.CharField(max_length=128, default="")
    enabled = fields.BooleanField(default=True)
    once = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "scheduled_tasks"


class UserAlias(Model):
    """User alias/nickname mapping for LLM-readable messages."""
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField()
    alias = fields.CharField(max_length=64)
    scope = fields.CharField(max_length=32, default="global")  # 'global' or 'ctx_{ctx_id}'

    class Meta:
        table = "user_aliases"
        unique_together = (("user_id", "scope"),)
