"""Config read/write API for the admin panel.

Editable fields are layered on top of config.yaml in the admin_config DB table.
The in-memory Config object is patched in-place on PATCH; the override is also
persisted so it survives restarts.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from yuubot.config import Config
from yuubot.core.models import AdminConfigKV

# Keys exposed to the admin UI (flat dot-notation).
# Infra keys (ports, paths) are intentionally excluded.
_EDITABLE: set[str] = {
    "response.group_default",
    "response.dm_whitelist",
    "memory.forget_days",
    "memory.max_length",
    "session.summarize_steps_span",
    "bot.entries",
    "agent_llm_refs",
}


def _get_nested(obj: Any, path: str) -> Any:
    parts = path.split(".")
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _set_nested(obj: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def _config_snapshot(config: Config) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in _EDITABLE:
        snapshot[key] = _get_nested(config, key)
    return snapshot


def _coerce(key: str, value: Any, config: Config) -> Any:
    current = _get_nested(config, key)
    if isinstance(current, list):
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        if current and isinstance(current[0], int):
            return [int(v) for v in value]
        return [str(v) for v in value]
    if isinstance(current, int):
        return int(value)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dict")
        return value
    return value


def create_config_router(config: Config, auth_dep) -> APIRouter:
    router = APIRouter(prefix="/api/config", dependencies=[Depends(auth_dep)])

    @router.get("")
    async def get_config() -> JSONResponse:
        return JSONResponse(_config_snapshot(config))

    @router.patch("")
    async def patch_config(body: dict[str, Any]) -> JSONResponse:
        errors: dict[str, str] = {}
        applied: dict[str, Any] = {}

        for key, value in body.items():
            if key not in _EDITABLE:
                errors[key] = "not editable"
                continue
            try:
                coerced = _coerce(key, value, config)
                _set_nested(config, key, coerced)
                await AdminConfigKV.update_or_create(
                    key=key, defaults={"value": json.dumps(coerced, ensure_ascii=False)}
                )
                applied[key] = coerced
            except (ValueError, TypeError, AttributeError) as exc:
                errors[key] = str(exc)

        return JSONResponse({"applied": applied, "errors": errors})

    return router


async def load_config_overrides(config: Config) -> None:
    """Apply persisted admin overrides onto the Config object at startup."""
    rows = await AdminConfigKV.all()
    for row in rows:
        if row.key not in _EDITABLE:
            continue
        try:
            value = json.loads(row.value)
            coerced = _coerce(row.key, value, config)
            _set_nested(config, row.key, coerced)
        except Exception:
            pass
