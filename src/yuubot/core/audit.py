"""Outbound message content audit — block sensitive info leaks."""

import re
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Sensitive patterns
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(
    r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])"
)

_IPV6_RE = re.compile(
    r"(?<![\w:])(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}(?![\w:])"          # full
    r"|(?<![\w:])(?:[0-9a-f]{1,4}:){1,7}:(?![\w:])"                    # trailing ::
    r"|(?<![\w:]):(?::[0-9a-f]{1,4}){1,7}(?![\w:])"                    # leading ::
    r"|(?<![\w:])(?:[0-9a-f]{1,4}:){1,6}:[0-9a-f]{1,4}(?![\w:])"      # middle ::
    r"|::(?:ffff:)?(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",                  # ::ffff:IPv4
    re.IGNORECASE,
)

_MAC_RE = re.compile(
    r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"
)

_LINUX_PATH_RE = re.compile(
    r"(?:/(?:home|root|etc|proc|sys|var|tmp|opt|usr|srv)/)\S+"
)

_WIN_PATH_RE = re.compile(
    r"[A-Z]:\\(?:Users|Windows|Program Files)\\\S+"
)

_API_KEY_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9]{20,}"          # OpenAI
    r"|ghp_[A-Za-z0-9]{36,}"        # GitHub PAT
    r"|gho_[A-Za-z0-9]{36,}"        # GitHub OAuth
    r"|AKIA[A-Z0-9]{16}"            # AWS Access Key
    r"|xox[bpsa]-[A-Za-z0-9\-]{10,}" # Slack
    r")\b"
)

_BEARER_RE = re.compile(
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",
    re.IGNORECASE,
)

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("IPv4地址", _IPV4_RE),
    ("IPv6地址", _IPV6_RE),
    ("MAC地址", _MAC_RE),
    ("系统文件路径", _LINUX_PATH_RE),
    ("Windows路径", _WIN_PATH_RE),
    ("API密钥", _API_KEY_RE),
    ("Bearer令牌", _BEARER_RE),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class AuditResult(NamedTuple):
    passed: bool
    category: str  # empty when passed


def _extract_text(segments: list[dict]) -> str:
    """Extract all plain text from OneBot V11 message segments."""
    parts: list[str] = []
    for seg in segments:
        if seg.get("type") == "text":
            data = seg.get("data", {})
            parts.append(data.get("text", ""))
    return " ".join(parts)


def audit_message(segments: list[dict]) -> AuditResult:
    """Check outbound message segments for sensitive content.

    Returns ``AuditResult(passed=True, category="")`` when safe,
    or ``AuditResult(passed=False, category="...")`` on violation.
    """
    text = _extract_text(segments)
    if not text:
        return AuditResult(passed=True, category="")

    for category, pattern in PATTERNS:
        if pattern.search(text):
            return AuditResult(passed=False, category=category)

    return AuditResult(passed=True, category="")


def audit_text(text: str) -> AuditResult:
    """Run both hard and soft audits on plain text."""
    segments = [{"type": "text", "data": {"text": text}}]
    result = audit_message(segments)
    if not result.passed:
        return result
    return soft_audit_message(segments)


# ---------------------------------------------------------------------------
# Soft audit — structured privacy data detection
# ---------------------------------------------------------------------------

_PRIVACY_KEYWORDS: set[str] = {
    "ip", "city", "region", "country", "org", "isp",
    "hostname", "timezone", "loc", "latitude", "longitude",
    "postal", "asn",
}

_PRIVACY_KEY_RE = re.compile(
    r'"(' + "|".join(_PRIVACY_KEYWORDS) + r')"',
    re.IGNORECASE,
)

_SOFT_AUDIT_THRESHOLD = 2


def soft_audit_message(segments: list[dict]) -> AuditResult:
    """Detect structured privacy data leaks (e.g. JSON with IP/geo fields).

    Flags messages containing >= 2 distinct privacy-related JSON keys.
    """
    text = _extract_text(segments)
    if not text:
        return AuditResult(passed=True, category="")

    hits = {m.group(1).lower() for m in _PRIVACY_KEY_RE.finditer(text)}
    if len(hits) >= _SOFT_AUDIT_THRESHOLD:
        return AuditResult(
            passed=False,
            category="疑似隐私数据泄露(检测到结构化IP/地理信息)",
        )

    return AuditResult(passed=True, category="")
