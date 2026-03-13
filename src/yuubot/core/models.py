"""Data models for messages, events, and contexts.

Wire-format models use msgspec.Struct.
Database models use tortoise.Model.
"""

from enum import IntEnum
from typing import Literal

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


class ReplySegment(msgspec.Struct, tag="reply"):
    id: str


Segment = TextSegment | ImageSegment | AtSegment | ReplySegment

Message = list[Segment]


def segments_to_plain(segments: Message) -> str:
    """Extract plain text from message segments."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSegment):
            parts.append(seg.text)
        elif isinstance(seg, AtSegment):
            parts.append(f"@{seg.qq}")
        elif isinstance(seg, ImageSegment):
            parts.append("[图片]")
        elif isinstance(seg, ReplySegment):
            parts.append(f"[回复:{seg.id}]")
    return "".join(parts)


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


# ── Role ─────────────────────────────────────────────────────────


class Role(IntEnum):
    DENY = 0
    FOLK = 1
    MOD = 2
    MASTER = 3


# ── Tortoise ORM Models ─────────────────────────────────────────


class Context(Model):
    id = fields.IntField(primary_key=True)
    type = fields.CharField(max_length=16)
    target_id = fields.BigIntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "contexts"
        unique_together = (("type", "target_id"),)


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


class RoleRecord(Model):
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField()
    role = fields.IntField()
    scope = fields.CharField(max_length=32)

    class Meta:
        table = "roles"
        unique_together = (("user_id", "scope"),)


class GroupSetting(Model):
    group_id = fields.BigIntField(primary_key=True)
    bot_enabled = fields.BooleanField(default=True)
    response_mode = fields.CharField(max_length=16, default="at")
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


class ImageEntry(Model):
    id = fields.IntField(primary_key=True)
    local_path = fields.CharField(max_length=512, unique=True)
    description = fields.TextField(default="")
    tags = fields.JSONField(default=list)
    source_msg_id = fields.BigIntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "images"


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


class AutoModeSetting(Model):
    """Persisted auto-mode state: which ctx has auto mode on, and which agent is selected."""
    ctx_id = fields.IntField(primary_key=True)
    current_agent = fields.CharField(max_length=64, default="")

    class Meta:
        table = "auto_mode"


class UserAlias(Model):
    """User alias/nickname mapping for LLM-readable messages."""
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField()
    alias = fields.CharField(max_length=64)
    scope = fields.CharField(max_length=32, default="global")  # 'global' or 'ctx_{ctx_id}'

    class Meta:
        table = "user_aliases"
        unique_together = (("user_id", "scope"),)
