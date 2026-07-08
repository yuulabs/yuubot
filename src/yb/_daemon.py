"""Shared HTTP helpers for yb facades talking to the local daemon."""

from __future__ import annotations

import os

import httpx


def daemon_url() -> str:
    url = os.getenv("YUUBOT_DAEMON_URL")
    if url:
        return url.rstrip("/")
    host = os.getenv("YUUBOT_SERVER_HOST", "127.0.0.1")
    port = os.getenv("YUUBOT_SERVER_PORT", "8765")
    return f"http://{host}:{port}"


def task_owner() -> str:
    owner = os.getenv("YUUBOT_TASK_OWNER")
    if not owner:
        raise RuntimeError("YUUBOT_TASK_OWNER is required for yb.tasks")
    return owner


async def request_json(
    method: str,
    url: str,
    params: dict[str, str] | None = None,
    json: dict[str, object] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, object]:
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, params=params, json=json, timeout=timeout_s)
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("unexpected daemon API response")
    return body
