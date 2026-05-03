"""Domain services for the RFC2 yuubot runtime."""

from yuubot.services.base import (
    AuditEvent,
    EmptyResult,
    InvalidScope,
    MediaRef,
    PageInfo,
    AccessDenied,
    Reference,
    ServiceNotImplementedError,
    YuubotServiceError,
)
from yuubot.services.delegate import DelegateService
from yuubot.services.im import ImService
from yuubot.services.media import MediaService
from yuubot.services.mem import MemoryService
from yuubot.services.web import WebService
from yuubot.services.workspace import WorkspaceService

__all__ = [
    "AuditEvent",
    "DelegateService",
    "EmptyResult",
    "ImService",
    "InvalidScope",
    "MediaRef",
    "MediaService",
    "MemoryService",
    "PageInfo",
    "AccessDenied",
    "Reference",
    "ServiceNotImplementedError",
    "WebService",
    "WorkspaceService",
    "YuubotServiceError",
]
