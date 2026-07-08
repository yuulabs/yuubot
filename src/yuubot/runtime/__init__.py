from .cache import CachePool
from .events import EventBus, ListenerHub, RuntimeEvent
from .inbound import (
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
from .mcp import (
    McpCapabilityIndex,
    McpCapabilitySummary,
    McpManager,
    McpResult,
    McpServerRecord,
    McpServerState,
)
from .skills import SkillInput, SkillRecord, SkillSummary
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
    TaskDelivery,
    TaskDeliveryListener,
    TaskRegistry,
    TaskScheduler,
    TaskSnapshot,
    register_shell_task,
    task_record_snapshot,
    wait_until_terminal_or_idle,
    wait_until_terminal_or_timeout,
)
from .wakeup import WakeupDelivery, WakeupPayload, WakeupTarget

__all__ = [
    "ActorMailboxRegistry",
    "ApplicationStateStore",
    "CachePool",
    "EventBus",
    "Gateway",
    "InboundEnvelope",
    "KvBadRequestError",
    "KvConflictError",
    "KvPutBody",
    "KvStore",
    "ListenerHub",
    "Mailbox",
    "MailboxUnavailableError",
    "McpCapabilityIndex",
    "McpCapabilitySummary",
    "McpManager",
    "McpResult",
    "McpServerRecord",
    "McpServerState",
    "Runtime",
    "RuntimeEvent",
    "RuntimeTaskRecord",
    "ShareBadRequestError",
    "ShareGrant",
    "ShareNotFoundError",
    "SharePublishError",
    "ShareRegistry",
    "SkillInput",
    "SkillRecord",
    "SkillSummary",
    "TaskCoroFactory",
    "TaskDelivery",
    "TaskDeliveryListener",
    "TaskRegistry",
    "TaskScheduler",
    "TaskSnapshot",
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
    "wait_until_terminal_or_idle",
    "wait_until_terminal_or_timeout",
]

_CORE_EXPORTS = frozenset({"ActorMailboxRegistry", "Gateway", "Mailbox", "Runtime"})
_STORE_EXPORTS = frozenset({"ApplicationStateStore"})


def __getattr__(name: str):
    if name in _CORE_EXPORTS:
        from .core import ActorMailboxRegistry, Gateway, Mailbox, Runtime

        return {
            "ActorMailboxRegistry": ActorMailboxRegistry,
            "Gateway": Gateway,
            "Mailbox": Mailbox,
            "Runtime": Runtime,
        }[name]
    if name in _STORE_EXPORTS:
        from .store import ApplicationStateStore

        return ApplicationStateStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
