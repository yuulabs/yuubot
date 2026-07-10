"""Typed runtime event payloads."""

from __future__ import annotations

from collections.abc import Callable

import msgspec

from ..domain.stream import StreamEvent

RuntimeEventKind = str
EmitFn = Callable[["RuntimeEventPayload"], None]


class ConversationInputPayload(msgspec.Struct, frozen=True, tag="conversation.input"):
    conversation_id: str
    content: list[object]


class ConversationStreamPayload(msgspec.Struct, frozen=True, tag="conversation.stream"):
    conversation_id: str
    event: StreamEvent


class ConversationOutputPayload(msgspec.Struct, frozen=True, tag="conversation.output"):
    conversation_id: str
    reason: str


class ConversationToolResultsPayload(msgspec.Struct, frozen=True, tag="conversation.tool_results"):
    conversation_id: str
    count: int
    results: list[object]


class ConversationHistoryAppendPayload(msgspec.Struct, frozen=True, tag="conversation.history.append"):
    conversation_id: str
    item: dict[str, object]


class ConversationUsagePayload(msgspec.Struct, frozen=True, tag="conversation.usage"):
    conversation_id: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    account: dict[str, object] = msgspec.field(default_factory=dict)


class ConversationToolProgressPayload(msgspec.Struct, frozen=True, tag="conversation.tool_progress"):
    conversation_id: str
    tool_call_id: str
    tool_name: str
    text: str = ""
    task: str = ""


class ActorBlockedPayload(msgspec.Struct, frozen=True, tag="actor.blocked"):
    actor_id: str
    conversation_id: str
    reason: str


class ActorBusyPayload(msgspec.Struct, frozen=True, tag="actor.busy"):
    actor_id: str
    conversation_id: str


class ActorOutputPayload(msgspec.Struct, frozen=True, tag="actor.output"):
    actor_id: str
    conversation_id: str
    outputs: int


class ActorContextCompactedPayload(msgspec.Struct, frozen=True, tag="actor.context_compacted"):
    actor_id: str
    old_conversation_id: str
    new_conversation_id: str
    input_tokens: int
    threshold: int


class ActorContextCompactionStoppedPayload(
    msgspec.Struct, frozen=True, tag="actor.context_compaction_stopped"
):
    actor_id: str
    conversation_id: str
    input_tokens: int
    threshold: int


class TaskStartedPayload(msgspec.Struct, frozen=True, tag="task.started"):
    task_id: str
    owner: str
    kind: str
    name: str


class TaskFinishedPayload(msgspec.Struct, frozen=True, tag="task.finished"):
    task_id: str
    owner: str
    kind: str
    status: str
    error: str | None = None
    exit_code: int | None = None


class TaskUsagePayload(msgspec.Struct, frozen=True, tag="task.usage"):
    task_id: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    account: dict[str, object] = msgspec.field(default_factory=dict)


class CronStartedPayload(msgspec.Struct, frozen=True, tag="cron.started"):
    job_id: str
    owner: str
    action_kind: str


class CronFailedPayload(msgspec.Struct, frozen=True, tag="cron.failed"):
    job_id: str
    owner: str


class CronFinishedPayload(msgspec.Struct, frozen=True, tag="cron.finished"):
    job_id: str
    owner: str
    action_kind: str


class ShareCreatedPayload(msgspec.Struct, frozen=True, tag="share.created"):
    share_id: str
    actor_id: str
    source_path: str


class ShareRevokedPayload(msgspec.Struct, frozen=True, tag="share.revoked"):
    share_id: str


class ShareExpiredPayload(msgspec.Struct, frozen=True, tag="share.expired"):
    share_id: str


class ResourceDiskCriticalPayload(msgspec.Struct, frozen=True, tag="resource.disk_critical"):
    disk_path: str
    disk_percent: float
    disk_free_bytes: int


class ResourceDiskWarningPayload(msgspec.Struct, frozen=True, tag="resource.disk_warning"):
    disk_path: str
    disk_percent: float
    disk_free_bytes: int


class ResourceDiskOkPayload(msgspec.Struct, frozen=True, tag="resource.disk_ok"):
    disk_path: str
    disk_percent: float
    disk_free_bytes: int


class IncomingMessagePayload(msgspec.Struct, frozen=True, tag="incoming.message"):
    route: str
    text: str
    source: dict[str, object] = msgspec.field(default_factory=dict)


class GatewayDispatchPayload(msgspec.Struct, frozen=True, tag="gateway.dispatch"):
    route: str
    actor_id: str | None
    delivered: bool
    conversation_id: str | None = None


class WakeupDeliveredPayload(msgspec.Struct, frozen=True, tag="wakeup.delivered"):
    actor_id: str
    inbound_kind: str


class NotificationDeliveredPayload(msgspec.Struct, frozen=True, tag="notification.delivered"):
    job_id: str
    title: str
    body: str
    meta: dict[str, object] = msgspec.field(default_factory=dict)


class TerminalOpenedPayload(msgspec.Struct, frozen=True, tag="terminal.opened"):
    auth_user: str
    cwd: str
    command: str


class TerminalClosedPayload(msgspec.Struct, frozen=True, tag="terminal.closed"):
    auth_user: str


RuntimeEventPayload = (
    ConversationInputPayload
    | ConversationStreamPayload
    | ConversationOutputPayload
    | ConversationToolResultsPayload
    | ConversationHistoryAppendPayload
    | ConversationUsagePayload
    | ConversationToolProgressPayload
    | ActorBlockedPayload
    | ActorBusyPayload
    | ActorOutputPayload
    | ActorContextCompactedPayload
    | ActorContextCompactionStoppedPayload
    | TaskStartedPayload
    | TaskFinishedPayload
    | TaskUsagePayload
    | CronStartedPayload
    | CronFailedPayload
    | CronFinishedPayload
    | ShareCreatedPayload
    | ShareRevokedPayload
    | ShareExpiredPayload
    | ResourceDiskCriticalPayload
    | ResourceDiskWarningPayload
    | ResourceDiskOkPayload
    | IncomingMessagePayload
    | GatewayDispatchPayload
    | WakeupDeliveredPayload
    | NotificationDeliveredPayload
    | TerminalOpenedPayload
    | TerminalClosedPayload
)

_PAYLOAD_KIND: dict[type[RuntimeEventPayload], RuntimeEventKind] = {
    ConversationInputPayload: "conversation.input",
    ConversationStreamPayload: "conversation.stream",
    ConversationOutputPayload: "conversation.output",
    ConversationToolResultsPayload: "conversation.tool_results",
    ConversationHistoryAppendPayload: "conversation.history.append",
    ConversationUsagePayload: "conversation.usage",
    ConversationToolProgressPayload: "conversation.tool_progress",
    ActorBlockedPayload: "actor.blocked",
    ActorBusyPayload: "actor.busy",
    ActorOutputPayload: "actor.output",
    ActorContextCompactedPayload: "actor.context_compacted",
    ActorContextCompactionStoppedPayload: "actor.context_compaction_stopped",
    TaskStartedPayload: "task.started",
    TaskFinishedPayload: "task.finished",
    TaskUsagePayload: "task.usage",
    CronStartedPayload: "cron.started",
    CronFailedPayload: "cron.failed",
    CronFinishedPayload: "cron.finished",
    ShareCreatedPayload: "share.created",
    ShareRevokedPayload: "share.revoked",
    ShareExpiredPayload: "share.expired",
    ResourceDiskCriticalPayload: "resource.disk_critical",
    ResourceDiskWarningPayload: "resource.disk_warning",
    ResourceDiskOkPayload: "resource.disk_ok",
    IncomingMessagePayload: "incoming.message",
    GatewayDispatchPayload: "gateway.dispatch",
    WakeupDeliveredPayload: "wakeup.delivered",
    NotificationDeliveredPayload: "notification.delivered",
    TerminalOpenedPayload: "terminal.opened",
    TerminalClosedPayload: "terminal.closed",
}


def event_kind(payload: RuntimeEventPayload) -> RuntimeEventKind:
    kind = _PAYLOAD_KIND.get(type(payload))
    if kind is None:
        raise TypeError(f"unknown runtime event payload type: {type(payload)!r}")
    return kind
