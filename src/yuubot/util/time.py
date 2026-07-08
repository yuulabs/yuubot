from datetime import UTC, datetime


def utc_now_iso(zulu: bool = False) -> str:
    value = datetime.now(UTC).isoformat()
    return value.replace("+00:00", "Z") if zulu else value
