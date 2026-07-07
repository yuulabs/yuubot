INTERNAL_ERROR_MESSAGE = "internal server error"


def internal_error_message(exc: Exception, *, development: bool) -> str:
    if not development:
        return INTERNAL_ERROR_MESSAGE
    message = str(exc).strip()
    return message or f"{type(exc).__name__}: {exc!r}"


def internal_error_detail(exc: Exception, *, development: bool) -> dict[str, object] | None:
    if not development:
        return None
    return {"type": type(exc).__name__}
