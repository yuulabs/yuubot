"""URL domain blocklist — block known IP/geolocation lookup services."""

import os
from urllib.parse import urlsplit

_BLOCKED_DOMAINS: set[str] = {
    "ipinfo.io",
    "ip-api.com",
    "ifconfig.me",
    "ifconfig.co",
    "icanhazip.com",
    "whatismyip.com",
    "myip.com",
    "ipify.org",
    "api.ipify.org",
    "checkip.amazonaws.com",
    "ipgeolocation.io",
    "ip.sb",
    "ipapi.co",
    "freegeoip.app",
    "geojs.io",
    "wtfismyip.com",
    "httpbin.org",
}


def _is_bot_mode() -> bool:
    return os.environ.get("YUU_IN_BOT", "").lower() in ("1", "true", "yes")


def _match_domain(hostname: str, blocked: str) -> bool:
    """Return True if hostname equals or is a subdomain of blocked."""
    return hostname == blocked or hostname.endswith("." + blocked)


def check_url(url: str) -> None:
    """Raise ValueError if URL targets a blocked domain (bot mode only)."""
    if not _is_bot_mode():
        return

    hostname = (urlsplit(url).hostname or "").lower()
    for domain in _BLOCKED_DOMAINS:
        if _match_domain(hostname, domain):
            raise ValueError(f"安全策略: 禁止访问IP/地理位置查询服务 ({domain})")
