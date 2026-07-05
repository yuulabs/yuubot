import msgspec
from fastapi.responses import Response


def json_response(payload: object, status: int = 200) -> Response:
    return Response(content=msgspec.json.encode(payload), status_code=status, media_type="application/json")


def error_response(status: int, code: str, message: str, detail: dict[str, object] | None = None) -> Response:
    error: dict[str, object] = {"code": code, "message": message}
    if detail:
        error["detail"] = detail
    return json_response({"error": error}, status=status)
