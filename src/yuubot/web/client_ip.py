"""Client IP resolution behind trusted reverse proxies."""

from collections.abc import Mapping
from typing import Any

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def trust_forwarded_headers(peer_host: str, trusted_proxies: frozenset[str]) -> bool:
    return is_loopback(peer_host) or peer_host in trusted_proxies


def resolve_client_ip(
    peer_host: str,
    forwarded_for: str | None,
    trusted_proxies: frozenset[str],
) -> str:
    if forwarded_for is None or not trust_forwarded_headers(peer_host, trusted_proxies):
        return peer_host
    chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if not chain:
        return peer_host
    for index, hop in enumerate(chain):
        if hop in trusted_proxies or is_loopback(hop):
            return chain[index - 1] if index > 0 else peer_host
    return chain[0]


def peer_host_from_scope(client: tuple[str, int] | None) -> str:
    if client is None:
        return ""
    return client[0]


def header_value(headers: Mapping[bytes, bytes], name: str) -> str | None:
    value = headers.get(name.lower().encode("ascii"))
    if value is None:
        return None
    text = value.decode("latin-1").strip()
    return text or None


def client_ip_from_scope(
    scope: Mapping[str, Any],
    trusted_proxies: frozenset[str],
) -> str:
    client = scope.get("client")
    peer = peer_host_from_scope(client if isinstance(client, tuple) else None)
    headers = scope.get("headers")
    if not isinstance(headers, list):
        return peer
    header_map = {name: value for name, value in headers}
    forwarded_for = header_value(header_map, "x-forwarded-for")
    return resolve_client_ip(peer, forwarded_for, trusted_proxies)
