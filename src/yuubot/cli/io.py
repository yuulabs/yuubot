import json
import sys

import msgspec


def emit(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(msgspec.json.encode(payload).decode())
        return
    if payload["ok"]:
        print("ok")
        for key, value in payload.items():
            if key != "ok" and not isinstance(value, (dict, list)):
                print(f"{key}: {value}")
        return
    error = payload["error"]
    if isinstance(error, dict):
        print(f"error: {error.get('code') or error.get('type')}: {error['message']}", file=sys.stderr)


def error_payload(exc: Exception) -> dict[str, object]:
    return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


def not_running_payload() -> dict[str, object]:
    return {"ok": False, "error": {"code": "service_not_running", "message": "yuubot is not running"}}


def admin_post(host: str, port: int, path: str, body: dict[str, object]) -> dict[str, object]:
    import urllib.request

    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=msgspec.json.encode(body),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read())
    if not isinstance(payload, dict):
        raise TypeError("admin response must be a JSON object")
    return payload
