"""Update status and apply result types."""

from __future__ import annotations

import msgspec


class UpdateStatus(msgspec.Struct, frozen=True, kw_only=True):
    supported: bool
    install_kind: str
    current_version: str
    current_commit: str | None = None
    remote_commit: str | None = None
    update_available: bool = False
    message: str = ""


class UpdateApplyResult(msgspec.Struct, frozen=True, kw_only=True):
    status: str
    log_path: str | None = None
    message: str = ""
