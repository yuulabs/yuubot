from .cache import CachePool
from .events import EventBus, ListenerHub, RuntimeEvent
from .inbound import (
    ActorInboundBody,
    InboundEnvelope,
    MailboxUnavailableError,
    deliver_actor_inbound,
    deliver_app_webhook,
)
from .kv import (
    KvBadRequestError,
    KvConflictError,
    KvPutBody,
    KvStore,
    document_snapshot,
    normalize_key,
    parse_if_match,
)
from .shares import (
    ShareBadRequestError,
    ShareGrant,
    ShareNotFoundError,
    SharePublishError,
    ShareRegistry,
    share_grant_snapshot,
)
from .streams import TaskCoroFactory, TextStream
from .tasks import (
    RuntimeTaskRecord,
    TaskDeliveryListener,
    TaskRegistry,
    TaskScheduler,
    register_shell_task,
    task_record_snapshot,
    wait_until_terminal_or_timeout,
)
from .wakeup import WakeupDelivery, WakeupPayload, WakeupTarget

__all__ = [
    "ActorInboundBody",
    "ActorMailboxRegistry",
    "ApplicationStateStore",
    "CachePool",
    "EventBus",
    "Gateway",
    "IncomingMessage",
    "InboundEnvelope",
    "KvBadRequestError",
    "KvConflictError",
    "KvPutBody",
    "KvStore",
    "ListenerHub",
    "Mailbox",
    "MailboxUnavailableError",
    "Runtime",
    "RuntimeEvent",
    "RuntimeTaskRecord",
    "ShareBadRequestError",
    "ShareGrant",
    "ShareNotFoundError",
    "SharePublishError",
    "ShareRegistry",
    "TaskCoroFactory",
    "TaskDeliveryListener",
    "TaskRegistry",
    "TaskScheduler",
    "TextStream",
    "WakeupDelivery",
    "WakeupPayload",
    "WakeupTarget",
    "deliver_actor_inbound",
    "deliver_app_webhook",
    "document_snapshot",
    "normalize_key",
    "parse_if_match",
    "register_shell_task",
    "share_grant_snapshot",
    "task_record_snapshot",
    "wait_until_terminal_or_timeout",
]

_CORE_EXPORTS = frozenset({"ActorMailboxRegistry", "Gateway", "IncomingMessage", "Mailbox", "Runtime"})
_STORE_EXPORTS = frozenset({"ApplicationStateStore"})


def __getattr__(name: str):
    if name in _CORE_EXPORTS:
        from .core import ActorMailboxRegistry, Gateway, IncomingMessage, Mailbox, Runtime

        return {
            "ActorMailboxRegistry": ActorMailboxRegistry,
            "Gateway": Gateway,
            "IncomingMessage": IncomingMessage,
            "Mailbox": Mailbox,
            "Runtime": Runtime,
        }[name]
    if name in _STORE_EXPORTS:
        from .store import ApplicationStateStore

        return ApplicationStateStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
