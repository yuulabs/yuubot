from __future__ import annotations

import httpx

from yuubot.core.http_client import QQ_DIRECT_PATTERNS, build_async_client


async def test_build_async_client_routes_qq_domains_directly():
    client = build_async_client(qq_direct=True)
    try:
        qq_transport = client._transport_for_url(
            httpx.URL("https://multimedia.nt.qq.com.cn/download")
        )
        other_transport = client._transport_for_url(httpx.URL("https://example.com"))
        qq_mount_transports = {
            transport
            for pattern, transport in client._mounts.items()
            if pattern.pattern in QQ_DIRECT_PATTERNS
        }

        assert qq_transport in qq_mount_transports
        assert qq_transport is not other_transport
    finally:
        await client.aclose()


def test_qq_direct_patterns_cover_primary_qq_hosts():
    assert "all://*qq.com" in QQ_DIRECT_PATTERNS
    assert "all://*qq.com.cn" in QQ_DIRECT_PATTERNS
