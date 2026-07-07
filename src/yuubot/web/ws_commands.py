"""WebSocket command wire types."""

from __future__ import annotations

import msgspec

from ..domain.messages import ContentItem


class ConversationSendPayload(msgspec.Struct, frozen=True, kw_only=True):
    actor_id: str
    conversation_id: str | None = None
    content: list[ContentItem]


class ConversationSendCommand(msgspec.Struct, frozen=True, kw_only=True, tag="conversation.send"):
    id: str | None = None
    payload: ConversationSendPayload


class RuntimeEventsSubscribePayload(msgspec.Struct, frozen=True, kw_only=True):
    kinds: list[str] = msgspec.field(default_factory=list)


class RuntimeEventsSubscribeCommand(msgspec.Struct, frozen=True, kw_only=True, tag="runtime.events.subscribe"):
    id: str | None = None
    payload: RuntimeEventsSubscribePayload = msgspec.field(default_factory=RuntimeEventsSubscribePayload)


class ConversationHistorySubscribePayload(msgspec.Struct, frozen=True, kw_only=True):
    conversation_id: str


class ConversationHistorySubscribeCommand(
    msgspec.Struct, frozen=True, kw_only=True, tag="conversation.history.subscribe"
):
    id: str | None = None
    payload: ConversationHistorySubscribePayload


class TaskSubscribePayload(msgspec.Struct, frozen=True, kw_only=True):
    task_id: str


class TaskSubscribeCommand(msgspec.Struct, frozen=True, kw_only=True, tag="task.subscribe"):
    id: str | None = None
    payload: TaskSubscribePayload


class TaskStdinPayload(msgspec.Struct, frozen=True, kw_only=True):
    task_id: str
    text: str


class TaskStdinCommand(msgspec.Struct, frozen=True, kw_only=True, tag="task.stdin"):
    id: str | None = None
    payload: TaskStdinPayload


class ConversationInterruptPayload(msgspec.Struct, frozen=True, kw_only=True):
    conversation_id: str


class ConversationInterruptCommand(msgspec.Struct, frozen=True, kw_only=True, tag="conversation.interrupt"):
    id: str | None = None
    payload: ConversationInterruptPayload


class TaskCancelPayload(msgspec.Struct, frozen=True, kw_only=True):
    task_id: str


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
