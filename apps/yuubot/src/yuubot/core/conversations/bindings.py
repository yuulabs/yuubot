"""Conversation request binding models."""

from __future__ import annotations

from dataclasses import dataclass

from yuubot.resources.records import ConversationRecord


@dataclass(frozen=True)
class ConversationSendBinding:
    """Binding fields carried on the first send request body.

    ``actor_id`` is required on first send. Other fields are retained as
    request-shape compatibility for callers, but actor-owned configuration is
    resolved from the actor live reference.
    """

    conversation_id: str
    actor_id: str
    capability_set_id: str = ""
    llm_backend_id: str = ""
    model: str = ""


@dataclass(frozen=True)
class ConversationUploadBinding:
    """Binding fields needed when uploading before the first send."""

    actor_id: str = ""


@dataclass(frozen=True)
class ConversationUploadedFile:
    """A user file stored under the conversation workspace upload directory."""

    name: str
    path: str
    url: str
    size: int
    content_type: str

    def prompt_line(self) -> str:
        return f"[User uploaded a file {self.name}, stored at {self.url}]"


@dataclass
class ConversationBindingConflict(Exception):
    conversation: ConversationRecord

    def __str__(self) -> str:
        return (
            f"conversation {self.conversation.conversation_id!r} already has "
            "messages and is bound to a different actor/binding"
        )

