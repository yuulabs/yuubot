"""WebSocket command wire types."""

from __future__ import annotations

from typing import Annotated

import msgspec

from ..domain.messages import ContentItem

NonEmptyStr = Annotated[str, msgspec.Meta(min_length=1)]


class ConversationSendPayload(msgspec.Struct, frozen=True, kw_only=True):
    actor_id: NonEmptyStr
    conversation_id: str | None = None
    content: list[ContentItem]


class ConversationSendCommand(msgspec.Struct, frozen=True, kw_only=True, tag="conversation.send"):
    id: str | None = None
    payload: ConversationSendPayload


class RuntimeEventsSubscribePayload(msgspec.Struct, frozen=True):
    kinds: list[str] = msgspec.field(default_factory=list)


class RuntimeEventsSubscribeCommand(msgspec.Struct, frozen=True, tag="runtime.events.subscribe"):
    id: str | None = None
    payload: RuntimeEventsSubscribePayload = msgspec.field(default_factory=RuntimeEventsSubscribePayload)


class ConversationHistorySubscribePayload(msgspec.Struct, frozen=True):
    conversation_id: NonEmptyStr


class ConversationHistorySubscribeCommand(
    msgspec.Struct, frozen=True, kw_only=True, tag="conversation.history.subscribe"
):
    id: str | None = None
    payload: ConversationHistorySubscribePayload


class TaskSubscribePayload(msgspec.Struct, frozen=True):
    task_id: NonEmptyStr


class TaskSubscribeCommand(msgspec.Struct, frozen=True, kw_only=True, tag="task.subscribe"):
    id: str | None = None
    payload: TaskSubscribePayload


class TaskStdinPayload(msgspec.Struct, frozen=True):
    task_id: NonEmptyStr
    text: NonEmptyStr


class TaskStdinCommand(msgspec.Struct, frozen=True, kw_only=True, tag="task.stdin"):
    id: str | None = None
    payload: TaskStdinPayload


class ConversationInterruptPayload(msgspec.Struct, frozen=True):
    conversation_id: NonEmptyStr


class ConversationInterruptCommand(msgspec.Struct, frozen=True, kw_only=True, tag="conversation.interrupt"):
    id: str | None = None
    payload: ConversationInterruptPayload


class TaskCancelPayload(msgspec.Struct, frozen=True):
    task_id: NonEmptyStr


class TaskCancelCommand(msgspec.Struct, frozen=True, kw_only=True, tag="task.cancel"):
    id: str | None = None
    payload: TaskCancelPayload


WSCommand = (
    ConversationSendCommand
    | RuntimeEventsSubscribeCommand
    | ConversationHistorySubscribeCommand
    | TaskSubscribeCommand
    | TaskStdinCommand
    | ConversationInterruptCommand
    | TaskCancelCommand
)
