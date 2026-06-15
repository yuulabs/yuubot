"""Type definitions for admin HTTP handler boundaries.

Plain data containers with no behaviour — typed schemas for
request deserialization, daemon client configuration, and
callable type aliases for dependency injection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Coroutine

import msgspec
import yuullm


class PluginInstallRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for plugin install requests."""

    source_path: str = ""
    install_environment: bool = True
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    integration_id: str = ""


class DaemonResponseData(msgspec.Struct, forbid_unknown_fields=False):
    """Typed extraction from daemon JSON responses."""

    data: object = None
    warnings: list[str] = msgspec.field(default_factory=list)
    detail: str = ""


@dataclass
class DaemonClient:
    base_url: str
    daemon_secret: str = ""


@dataclass
class DaemonResponse:
    status_code: int
    body: bytes
    content_type: str = "application/json"


# -- Callable type aliases for dependency injection --
RequestDaemonFn = Callable[..., Coroutine[Any, Any, DaemonResponse]]
CreateProviderModelClientFn = Callable[..., yuullm.Provider]
