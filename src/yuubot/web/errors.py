import logging

from fastapi import Request
from fastapi.responses import Response

from .responses import error_response

INTERNAL_ERROR_MESSAGE = "internal server error"

_log = logging.getLogger(__name__)


def internal_error_message(exc: Exception, development: bool) -> str:
    if not development:
        return INTERNAL_ERROR_MESSAGE
    message = str(exc).strip()
    return message or f"{type(exc).__name__}: {exc!r}"


def internal_error_detail(exc: Exception, development: bool) -> dict[str, object] | None:
    if not development:
        return None
    return {"type": type(exc).__name__}


def log_internal_error(logger: logging.Logger, exc: Exception, context: str) -> None:
    logger.error(
        "%s failed with %s: %s",
        context,
        type(exc).__name__,
        internal_error_message(exc, True),
        exc_info=(type(exc), exc, exc.__traceback__),
    )


async def unhandled_exception_response(request: Request, exc: Exception, development: bool) -> Response:
    log_internal_error(_log, exc, f"Unhandled {request.method} {request.url.path}")
    return error_response(
        500,
        "internal_error",
        internal_error_message(exc, development),
        internal_error_detail(exc, development),
    )
