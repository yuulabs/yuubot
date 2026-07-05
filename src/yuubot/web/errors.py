INTERNAL_ERROR_MESSAGE = "internal server error"


def internal_error_message(exc: Exception, *, development: bool) -> str:
    return str(exc) if development else INTERNAL_ERROR_MESSAGE


def internal_error_detail(exc: Exception, *, development: bool) -> dict[str, object] | None:
    if not development:
        return None
    return {"type": type(exc).__name__}
