import msgspec
from fastapi import Request

from fastapi.responses import Response

from .responses import error_response


async def read_json[T](request: Request, type_: type[T]) -> T:
    return msgspec.json.decode(await request.body(), type=type_)


def bad_request(exc: Exception) -> Response:
    return error_response(400, "bad_request", str(exc))
