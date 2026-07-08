import re
from collections.abc import Mapping

SECRET_FIELD_RE = re.compile(r"(api_)?key|token|secret|password", re.IGNORECASE)
REDACTED = "***"

_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-or-v1-[a-zA-Z0-9_-]+"),
    re.compile(r"sk-kimi-[a-zA-Z0-9_-]+"),
    re.compile(r"sk-[a-zA-Z0-9_-]{8,}"),
    re.compile(r"rt\.[0-9]\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
)


def is_secret_field(key: str, secret_fields: frozenset[str] | None = None) -> bool:
    if secret_fields is not None and key in secret_fields:
        return True
    return SECRET_FIELD_RE.search(key) is not None


def redact_config(config: dict[str, object], secret_fields: frozenset[str] | None = None) -> dict[str, object]:
    return {
        key: "***" if is_secret_field(key, secret_fields) and value else value
        for key, value in config.items()
    }


def merge_redacted_config(
    incoming: dict[str, object],
    stored: dict[str, object] | None,
    secret_fields: frozenset[str] | None = None,
) -> dict[str, object]:
    merged = dict(stored or {})
    merged.update(incoming)
    if stored is None:
        return merged
    keys = set(incoming) | set(stored)
    for key in keys:
        if not is_secret_field(key, secret_fields):
            continue
        value = incoming.get(key)
        if value is None or value == "***":
            if key in stored:
                merged[key] = stored[key]
            else:
                merged.pop(key, None)
        elif value == "":
            merged[key] = ""
    return merged


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_secret_field(str(key)) and item else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value
