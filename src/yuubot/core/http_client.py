"""Shared HTTP client helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

QQ_DIRECT_PATTERNS = (
    "all://*qq.com",
    "all://*qq.com.cn",
    "all://*qpic.cn",
    "all://*gtimg.cn",
)


def _qq_direct_mounts() -> dict[str, httpx.AsyncBaseTransport]:
    return {
        pattern: httpx.AsyncHTTPTransport(trust_env=False)
        for pattern in QQ_DIRECT_PATTERNS
    }


def build_async_client(
    *,
    qq_direct: bool = False,
    mounts: Mapping[str, httpx.AsyncBaseTransport | None] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` with optional QQ-domain direct routing."""

    merged_mounts = dict(mounts or {})
    if qq_direct:
        for pattern, transport in _qq_direct_mounts().items():
            merged_mounts.setdefault(pattern, transport)
    if merged_mounts:
        kwargs["mounts"] = merged_mounts
    return httpx.AsyncClient(**kwargs)
