import re

SECRET_FIELD_RE = re.compile(r"(api_)?key|token|secret|password", re.IGNORECASE)


def is_secret_field(key: str, *, secret_fields: frozenset[str] | None = None) -> bool:
    if secret_fields is not None and key in secret_fields:
        return True
    return SECRET_FIELD_RE.search(key) is not None


def redact_config(config: dict[str, object], *, secret_fields: frozenset[str] | None = None) -> dict[str, object]:
    return {
        key: "***" if is_secret_field(key, secret_fields=secret_fields) and value else value
        for key, value in config.items()
    }


def merge_redacted_config(
    incoming: dict[str, object],
    stored: dict[str, object] | None,
    *,
    secret_fields: frozenset[str] | None = None,
) -> dict[str, object]:
    merged = dict(stored or {})
    merged.update(incoming)
    if stored is None:
        return merged
    keys = set(incoming) | set(stored)
    for key in keys:
        if not is_secret_field(key, secret_fields=secret_fields):
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
